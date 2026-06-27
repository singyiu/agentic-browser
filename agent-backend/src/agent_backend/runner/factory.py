"""Select the browser runner from ``config.ai_provider``.

Runners are imported lazily so a ``codex`` install never imports ``claude_agent_sdk`` (and
vice-versa). ``query_fn`` is forwarded to the Claude runner only (its test seam); ``config_dir``
is the Claude-only isolated ``CLAUDE_CONFIG_DIR`` and is ignored by Codex.
"""

from __future__ import annotations

from typing import Any

from ..config import RunnerConfig
from .base import BrowserAgentRunner


def build_runner(
    config: RunnerConfig,
    config_dir: str,
    *,
    query_fn: Any = None,
    python_executable: str | None = None,
) -> BrowserAgentRunner:
    if config.ai_provider == "codex":
        from .codex_runner import CodexRunner

        return CodexRunner(config, python_executable=python_executable)
    from .claude_runner import ClaudeRunner

    return ClaudeRunner(config, config_dir, query_fn=query_fn, python_executable=python_executable)
