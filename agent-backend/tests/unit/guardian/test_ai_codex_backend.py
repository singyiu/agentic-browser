"""Unit tests for CodexBackend (fake subprocess; no real codex binary)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from agent_backend.guardian.ai.backends.codex import CodexBackend
from agent_backend.guardian.config import GuardianConfig


def _config(tmp_path: Path, **over: Any) -> GuardianConfig:
    base: dict[str, Any] = dict(
        host="127.0.0.1",
        port=2947,
        metrics_port=2948,
        token="s",
        cache_path=str(tmp_path / "c.db"),
        event_log_path=str(tmp_path / "e.jsonl"),
        whitelist_path=str(tmp_path / "wl.json"),
        blocklist_path=str(tmp_path / "bl.json"),
        requests_path=str(tmp_path / "req.json"),
        parent_pin="pin",
        classify_timeout_s=5.0,
        screenshot_confidence_threshold=0.6,
        enable_vision=False,
        model="gpt-5-codex",
        config_dir="",
        oauth_token="",
        ai_provider="codex",
        codex_home=str(tmp_path / "codex-config"),
    )
    base.update(over)
    return GuardianConfig(**base)


class _FakeProc:
    def __init__(
        self, *, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0, hang: bool = False
    ) -> None:
        self._stdout, self._stderr = stdout, stderr
        self.returncode = returncode
        self._hang = hang
        self.killed = False
        self.stdin_data: bytes | None = None

    async def communicate(self, data: bytes | None = None) -> tuple[bytes, bytes]:
        self.stdin_data = data
        if self._hang:
            await asyncio.sleep(3600)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

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


async def test_complete_returns_stdout(tmp_path: Path) -> None:
    proc = _FakeProc(stdout=b'{"verdict":"allow"}')
    backend = CodexBackend(_config(tmp_path), subprocess_fn=_fake_exec(proc, {}))
    out = await backend.complete(system_prompt="sys", user_prompt="user", model=None)
    assert out == '{"verdict":"allow"}'


async def test_complete_combines_prompts_into_stdin(tmp_path: Path) -> None:
    proc = _FakeProc(stdout=b"ok")
    backend = CodexBackend(_config(tmp_path), subprocess_fn=_fake_exec(proc, {}))
    await backend.complete(system_prompt="SYS", user_prompt="USER", model=None)
    assert proc.stdin_data == b"SYS\n\nUSER"


async def test_complete_argv_has_required_flags(tmp_path: Path) -> None:
    capture: dict[str, Any] = {}
    backend = CodexBackend(
        _config(tmp_path), subprocess_fn=_fake_exec(_FakeProc(stdout=b"ok"), capture)
    )
    await backend.complete(system_prompt="s", user_prompt="u", model=None)
    argv = capture["argv"]
    assert argv[:3] == ["codex", "exec", "-"]
    for flag in (
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--ask-for-approval",
        "never",
        "--color",
        "never",
        "--ephemeral",
    ):
        assert flag in argv
    assert argv[-2:] == ["-m", "gpt-5-codex"]


async def test_complete_uses_model_override(tmp_path: Path) -> None:
    capture: dict[str, Any] = {}
    backend = CodexBackend(
        _config(tmp_path), subprocess_fn=_fake_exec(_FakeProc(stdout=b"ok"), capture)
    )
    await backend.complete(system_prompt="s", user_prompt="u", model="custom-model")
    assert "custom-model" in capture["argv"]
    assert "gpt-5-codex" not in capture["argv"]


async def test_complete_sets_codex_home_env(tmp_path: Path) -> None:
    capture: dict[str, Any] = {}
    cfg = _config(tmp_path)
    backend = CodexBackend(cfg, subprocess_fn=_fake_exec(_FakeProc(stdout=b"ok"), capture))
    await backend.complete(system_prompt="s", user_prompt="u", model=None)
    assert capture["env"]["CODEX_HOME"] == cfg.codex_home


async def test_complete_raises_on_nonzero_exit(tmp_path: Path) -> None:
    proc = _FakeProc(stdout=b"", stderr=b"boom", returncode=1)
    backend = CodexBackend(_config(tmp_path), subprocess_fn=_fake_exec(proc, {}))
    with pytest.raises(RuntimeError, match="codex exec exited 1"):
        await backend.complete(system_prompt="s", user_prompt="u", model=None)


async def test_complete_raises_on_empty_stdout(tmp_path: Path) -> None:
    proc = _FakeProc(stdout=b"   ")
    backend = CodexBackend(_config(tmp_path), subprocess_fn=_fake_exec(proc, {}))
    with pytest.raises(RuntimeError, match="empty output"):
        await backend.complete(system_prompt="s", user_prompt="u", model=None)


async def test_complete_raises_and_kills_on_timeout(tmp_path: Path) -> None:
    proc = _FakeProc(hang=True)
    backend = CodexBackend(
        _config(tmp_path, classify_timeout_s=0.05), subprocess_fn=_fake_exec(proc, {})
    )
    with pytest.raises(RuntimeError, match="timed out"):
        await backend.complete(system_prompt="s", user_prompt="u", model=None)
    assert proc.killed is True
