"""Select the completion backend from ``config.ai_provider``.

Backends are imported lazily so a ``codex`` install never imports ``claude_agent_sdk`` (and
vice-versa). ``query_fn`` is forwarded to the Claude backend only (its test seam).
"""

from __future__ import annotations

from typing import Any

from ..config import GuardianConfig
from .backend import CompletionBackend


def build_backend(config: GuardianConfig, *, query_fn: Any = None) -> CompletionBackend:
    if config.ai_provider == "codex":
        from .backends.codex import CodexBackend

        return CodexBackend(config)
    from .backends.claude import ClaudeBackend

    return ClaudeBackend(config, query_fn=query_fn)
