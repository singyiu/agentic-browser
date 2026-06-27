"""Codex (ChatGPT subscription) browser runner: ``codex exec --json``.

Codex drives the same browser-control MCP server, wired in per-call via ``-c`` config
overrides (no persisted config mutation). The combined system+task prompt is piped to stdin;
Codex emits a JSONL event stream on stdout which is mapped to the shared event sink.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from ..config import RunnerConfig
from .base import SYSTEM_PROMPT, EventSink, emit

_STDERR_TAIL = 500
# MCP tool calls aren't codex shell/FS ops, so a read-only sandbox is enough; -a never keeps
# the loop autonomous. Bump --sandbox to workspace-write if a codex build gates MCP calls.
_BASE_FLAGS = (
    "--json",
    "--ask-for-approval",
    "never",
    "--skip-git-repo-check",
    "--sandbox",
    "read-only",
    "--color",
    "never",
)


def _mcp_overrides(python: str, cdp_url: str) -> list[str]:
    """`-c` config overrides that register the browser MCP server for this run only."""
    return [
        "-c",
        f'mcp_servers.browser.command="{python}"',
        "-c",
        'mcp_servers.browser.args=["-m", "agent_backend.mcp_server"]',
        "-c",
        f'mcp_servers.browser.env.CHROMIUM_CDP_URL="{cdp_url}"',
    ]


def _describe_item(item: dict[str, Any]) -> str:
    itype = str(item.get("type", "item"))
    if itype == "command_execution":
        return f"command: {item.get('command', '')}"
    if itype == "mcp_tool_call":
        args = item.get("arguments", item.get("input", ""))
        return f"{item.get('server', '')}.{item.get('tool', '')} {args}".strip()
    return itype


def _handle_event(event: dict[str, Any], on_event: EventSink | None, final: str) -> str:
    """Map one Codex JSONL event to the sink; return the (possibly updated) final text."""
    etype = event.get("type")
    if etype == "item.completed":
        item = event.get("item") or {}
        itype = item.get("type")
        if itype == "agent_message":
            text = (item.get("text") or "").strip()
            if text:
                emit(on_event, text)
                return text
        elif itype in ("command_execution", "mcp_tool_call", "file_change", "web_search"):
            emit(on_event, f"[tool] {_describe_item(item)}")
    elif etype == "turn.completed":
        usage = event.get("usage") or {}
        if usage:
            emit(
                on_event,
                f"[cost] tokens in={usage.get('input_tokens', '?')} "
                f"out={usage.get('output_tokens', '?')}",
            )
    elif etype in ("error", "turn.failed"):
        emit(on_event, f"[error] {event.get('message') or event.get('error') or event}")
    return final


class CodexRunner:
    """``BrowserAgentRunner`` backed by the ``codex`` CLI.

    ``subprocess_fn`` is injectable for tests (defaults to ``asyncio.create_subprocess_exec``).
    """

    def __init__(
        self,
        config: RunnerConfig,
        *,
        subprocess_fn: Any = None,
        python_executable: str | None = None,
    ) -> None:
        self._config = config
        self._subprocess_fn = subprocess_fn or asyncio.create_subprocess_exec
        self._python = python_executable

    def _argv(self) -> list[str]:
        python = self._python or sys.executable
        return [
            "codex",
            "exec",
            "-",
            *_BASE_FLAGS,
            "-m",
            self._config.model,
            *_mcp_overrides(python, self._config.cdp_url),
        ]

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self._config.codex_home:
            env["CODEX_HOME"] = self._config.codex_home
        return env

    async def run_task(self, task: str, *, on_event: EventSink | None = None) -> str:
        prompt = f"{SYSTEM_PROMPT}\n\nTASK: {task}"
        proc = await self._subprocess_fn(
            *self._argv(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env(),
        )
        if proc.stdin is not None:
            proc.stdin.write(prompt.encode())
            await proc.stdin.drain()
            proc.stdin.close()

        final = ""
        stderr_chunks: list[bytes] = []

        async def _consume_stdout() -> None:
            nonlocal final
            if proc.stdout is None:
                return
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    final = _handle_event(event, on_event, final)

        async def _drain_stderr() -> None:
            if proc.stderr is None:
                return
            async for chunk in proc.stderr:
                stderr_chunks.append(chunk)

        # Drain both pipes concurrently so a full stderr buffer can't deadlock stdout.
        await asyncio.gather(_consume_stdout(), _drain_stderr())
        await proc.wait()
        if proc.returncode and not final:
            tail = b"".join(stderr_chunks).decode(errors="replace").strip()[-_STDERR_TAIL:]
            raise RuntimeError(f"codex exec exited {proc.returncode}: {tail}")
        return final
