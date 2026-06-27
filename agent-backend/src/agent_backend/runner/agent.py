"""Provider-agnostic facade for the autonomous browser runner.

Picks the Claude or Codex runner from ``config.ai_provider`` and delegates. ``query`` is kept
as a module global so tests can monkeypatch it; ``run_task`` passes it through to the Claude
runner at call time. The runner's public surface is re-exported for backward-compatible imports.
"""

from __future__ import annotations

from claude_agent_sdk import query

from ..config import RunnerConfig
from .base import (
    BROWSER_TOOLS,
    SERVER_NAME,
    SYSTEM_PROMPT,
    BrowserAgentRunner,
    EventSink,
    allowed_tools,
)
from .claude_runner import build_options
from .factory import build_runner

__all__ = [
    "BROWSER_TOOLS",
    "SERVER_NAME",
    "SYSTEM_PROMPT",
    "BrowserAgentRunner",
    "EventSink",
    "allowed_tools",
    "build_options",
    "build_runner",
    "query",
    "run_task",
]


async def run_task(
    task: str,
    config: RunnerConfig,
    config_dir: str,
    *,
    on_event: EventSink | None = None,
    python_executable: str | None = None,
) -> str:
    """Run one autonomous task on the configured provider; return the final result text."""
    runner = build_runner(config, config_dir, query_fn=query, python_executable=python_executable)
    return await runner.run_task(task, on_event=on_event)
