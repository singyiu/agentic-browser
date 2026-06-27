"""Shared pieces of the autonomous browser runner (provider-agnostic).

Both the Claude and Codex runners drive the same browser-control MCP server
(``python -m agent_backend.mcp_server``) and share the same system prompt and event sink.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

SERVER_NAME = "browser"
BROWSER_TOOLS = (
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_read",
    "browser_wait_for",
    "browser_back",
    "browser_screenshot",
)
# Built-in tools the agent must not touch — this is a browser-only agent.
_DISALLOWED = ["Bash", "Edit", "Write", "Read", "NotebookEdit", "WebFetch", "WebSearch", "Task"]

SYSTEM_PROMPT = (
    "You are an autonomous web agent controlling a single Chromium tab. Work in a loop: "
    "call browser_snapshot to read the page's accessibility tree, decide the next action, then "
    "act with browser_navigate / browser_click / browser_type / browser_read / browser_wait_for / "
    "browser_back. Prefer targeting elements by ARIA role + name; fall back to a CSS selector or "
    "visible text. Re-snapshot to verify after acting. Use ONLY the browser_* tools. When the task "
    "is complete, stop and report concisely what you did and what you observed."
)

EventSink = Callable[[str], None]


def allowed_tools() -> list[str]:
    """MCP tool names as namespaced by Claude Code: ``mcp__<server>__<tool>``."""
    return [f"mcp__{SERVER_NAME}__{tool}" for tool in BROWSER_TOOLS]


def emit(sink: EventSink | None, text: str) -> None:
    """Send one progress line to the sink (defaults to ``print`` for the CLI)."""
    (sink or print)(text)


class BrowserAgentRunner(Protocol):
    """One autonomous task: drive the browser, stream progress, return the final result text."""

    async def run_task(self, task: str, *, on_event: EventSink | None = None) -> str: ...
