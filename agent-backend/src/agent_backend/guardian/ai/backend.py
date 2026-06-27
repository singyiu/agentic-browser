"""The narrow provider seam: turn a prompt pair into model text."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CompletionBackend(Protocol):
    """One stateless completion: ``(system_prompt, user_prompt) -> text``.

    Implementations are provider-specific (Claude SDK, Codex CLI, …). They do NOT parse
    verdicts or fail open — that policy lives in ``Classifier``. Transport/process errors
    propagate so the caller can decide (classify fails open, generate surfaces a 502).
    """

    async def complete(self, *, system_prompt: str, user_prompt: str, model: str | None) -> str: ...
