"""Unit tests for CodexRunner (fake subprocess; no real codex binary)."""

from __future__ import annotations

from typing import Any

import pytest

from agent_backend.config import RunnerConfig
from agent_backend.runner.base import SYSTEM_PROMPT
from agent_backend.runner.codex_runner import CodexRunner


def _config(**over: Any) -> RunnerConfig:
    base: dict[str, Any] = dict(
        oauth_token="",
        model="gpt-5-codex",
        cdp_url="http://127.0.0.1:9222",
        ai_provider="codex",
        codex_home="/tmp/codex-config",
    )
    base.update(over)
    return RunnerConfig(**base)


class _Stdin:
    def __init__(self) -> None:
        self.buf = b""

    def write(self, data: bytes) -> None:
        self.buf += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


class _AsyncLines:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    def __aiter__(self) -> _AsyncLines:
        return self

    async def __anext__(self) -> bytes:
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeProc:
    def __init__(
        self,
        stdout_lines: list[bytes],
        stderr_lines: list[bytes] | None = None,
        returncode: int = 0,
    ) -> None:
        self.stdin = _Stdin()
        self.stdout = _AsyncLines(stdout_lines)
        self.stderr = _AsyncLines(stderr_lines or [])
        self.returncode = returncode

    async def wait(self) -> int:
        return self.returncode


def _fake_exec(proc: _FakeProc, capture: dict[str, Any]) -> Any:
    async def run(
        *argv: str, stdin: Any = None, stdout: Any = None, stderr: Any = None, env: Any = None
    ) -> _FakeProc:
        capture["argv"] = list(argv)
        capture["env"] = env
        return proc

    return run


_STREAM = [
    b'{"type":"thread.started","thread_id":"x"}\n',
    b'{"type":"item.completed","item":{"type":"command_execution","command":"ls"}}\n',
    b'{"type":"item.completed","item":{"type":"agent_message","text":"Done: H1 is Hi"}}\n',
    b'{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}\n',
]


async def test_run_task_returns_final_agent_message() -> None:
    runner = CodexRunner(_config(), subprocess_fn=_fake_exec(_FakeProc(_STREAM), {}))
    events: list[str] = []
    result = await runner.run_task("open example.com", on_event=events.append)
    assert result == "Done: H1 is Hi"
    assert any("[tool]" in e for e in events)
    assert any("[cost]" in e for e in events)
    assert any("Done: H1 is Hi" in e for e in events)


async def test_run_task_writes_prompt_to_stdin() -> None:
    proc = _FakeProc(_STREAM)
    runner = CodexRunner(_config(), subprocess_fn=_fake_exec(proc, {}))
    await runner.run_task("do a thing")
    assert proc.stdin.buf.startswith(SYSTEM_PROMPT.encode())
    assert b"TASK: do a thing" in proc.stdin.buf


async def test_run_task_argv_has_json_and_mcp_overrides() -> None:
    capture: dict[str, Any] = {}
    runner = CodexRunner(
        _config(),
        subprocess_fn=_fake_exec(_FakeProc(_STREAM), capture),
        python_executable="/usr/bin/python3",
    )
    await runner.run_task("t")
    argv = capture["argv"]
    assert argv[:3] == ["codex", "exec", "-"]
    assert "--json" in argv
    assert "-m" in argv and "gpt-5-codex" in argv
    assert 'mcp_servers.browser.command="/usr/bin/python3"' in argv
    assert 'mcp_servers.browser.args=["-m", "agent_backend.mcp_server"]' in argv
    assert 'mcp_servers.browser.env.CHROMIUM_CDP_URL="http://127.0.0.1:9222"' in argv
    # Trust the browser server so non-interactive `codex exec` auto-approves its tool calls.
    assert 'mcp_servers.browser.default_tools_approval_mode="approve"' in argv
    # `--ask-for-approval` was removed from `codex exec` in CLI 0.142+ (regression guard).
    assert "--ask-for-approval" not in argv


async def test_run_task_sets_codex_home_env() -> None:
    capture: dict[str, Any] = {}
    runner = CodexRunner(
        _config(codex_home="/tmp/ch"), subprocess_fn=_fake_exec(_FakeProc(_STREAM), capture)
    )
    await runner.run_task("t")
    assert capture["env"]["CODEX_HOME"] == "/tmp/ch"


async def test_run_task_emits_error_event() -> None:
    proc = _FakeProc([b'{"type":"error","message":"auth failed"}\n'])
    runner = CodexRunner(_config(), subprocess_fn=_fake_exec(proc, {}))
    events: list[str] = []
    result = await runner.run_task("t", on_event=events.append)
    assert result == ""
    assert any("[error]" in e and "auth failed" in e for e in events)


async def test_run_task_raises_on_failure_with_no_message() -> None:
    proc = _FakeProc([], stderr_lines=[b"codex: not signed in\n"], returncode=1)
    runner = CodexRunner(_config(), subprocess_fn=_fake_exec(proc, {}))
    with pytest.raises(RuntimeError, match="codex exec exited 1"):
        await runner.run_task("t")


async def test_run_task_tolerates_noise_and_reports_mcp_tool_calls() -> None:
    lines = [
        b"\n",  # blank line ignored
        b"not json at all\n",  # malformed line ignored
        b'{"type":"item.completed","item":{"type":"mcp_tool_call",'
        b'"server":"browser","tool":"browser_click","arguments":{"selector":"a"}}}\n',
        b'{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n',
    ]
    runner = CodexRunner(_config(), subprocess_fn=_fake_exec(_FakeProc(lines), {}))
    events: list[str] = []
    result = await runner.run_task("t", on_event=events.append)
    assert result == "ok"
    assert any("[tool] browser.browser_click" in e for e in events)
