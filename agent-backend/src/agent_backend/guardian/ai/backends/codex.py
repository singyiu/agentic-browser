"""Codex (ChatGPT subscription) backend: headless ``codex exec`` subprocess.

The combined system+user prompt is piped to ``codex exec -`` (stdin); the final agent message
is read from stdout (Codex sends progress to stderr). Auth lives in ``$CODEX_HOME/auth.json``
(written by ``codex login`` and refreshed in place), so no token is passed here. This module is
imported only when the active provider is ``codex``.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from ...config import GuardianConfig

# Deterministic, side-effect-free, non-interactive completion: read prompt from stdin (-),
# never edit files (read-only sandbox), never pause for approval, no ANSI, no persisted
# session rollout files, and allow running outside a git repo.
_BASE_FLAGS = (
    "--skip-git-repo-check",
    "--sandbox",
    "read-only",
    "--ask-for-approval",
    "never",
    "--color",
    "never",
    "--ephemeral",
)
_STDERR_TAIL = 500  # chars of codex stderr surfaced in error messages


class CodexBackend:
    """``CompletionBackend`` backed by the ``codex`` CLI.

    ``subprocess_fn`` is injectable for tests (defaults to ``asyncio.create_subprocess_exec``),
    so no real ``codex`` binary is needed to exercise this class.
    """

    def __init__(self, config: GuardianConfig, *, subprocess_fn: Any = None) -> None:
        self._config = config
        self._subprocess_fn = subprocess_fn or asyncio.create_subprocess_exec

    def _argv(self, model: str | None) -> list[str]:
        # Fresh list per call (no shared mutable state).
        return ["codex", "exec", "-", *_BASE_FLAGS, "-m", model or self._config.model]

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self._config.codex_home:
            env["CODEX_HOME"] = self._config.codex_home
        return env

    async def complete(self, *, system_prompt: str, user_prompt: str, model: str | None) -> str:
        combined = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt
        proc = await self._subprocess_fn(
            *self._argv(model),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(combined.encode()),
                timeout=self._config.classify_timeout_s,
            )
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"codex exec timed out after {self._config.classify_timeout_s}s"
            ) from exc
        if proc.returncode != 0:
            tail = stderr.decode(errors="replace").strip()[-_STDERR_TAIL:]
            raise RuntimeError(f"codex exec exited {proc.returncode}: {tail}")
        result: str = stdout.decode(errors="replace").strip()
        if not result:
            tail = stderr.decode(errors="replace").strip()[-_STDERR_TAIL:]
            raise RuntimeError(f"codex exec produced empty output. stderr: {tail}")
        return result
