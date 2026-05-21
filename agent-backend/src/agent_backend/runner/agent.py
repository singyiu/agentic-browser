"""Claude Agent SDK runner.

Drives the autonomous loop with the user's Claude Max subscription
(``CLAUDE_CODE_OAUTH_TOKEN``), restricted to the browser-control MCP tools, with
config/skills isolated via ``CLAUDE_CONFIG_DIR``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

from ..config import RunnerConfig

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


def build_options(
    config: RunnerConfig,
    config_dir: str,
    *,
    python_executable: str | None = None,
) -> ClaudeAgentOptions:
    """Assemble SDK options: isolated config, subscription auth, browser-only tools."""
    python = python_executable or sys.executable
    return ClaudeAgentOptions(
        model=config.model,
        system_prompt=SYSTEM_PROMPT,
        allowed_tools=allowed_tools(),
        disallowed_tools=list(_DISALLOWED),
        permission_mode="bypassPermissions",
        setting_sources=["user"],
        mcp_servers={
            SERVER_NAME: {
                "type": "stdio",
                "command": python,
                "args": ["-m", "agent_backend.mcp_server"],
                "env": {"CHROMIUM_CDP_URL": config.cdp_url},
            }
        },
        env={
            "CLAUDE_CONFIG_DIR": config_dir,
            "CLAUDE_CODE_OAUTH_TOKEN": config.oauth_token,
        },
    )


def _emit(sink: EventSink | None, text: str) -> None:
    (sink or print)(text)


async def run_task(
    task: str,
    config: RunnerConfig,
    config_dir: str,
    *,
    on_event: EventSink | None = None,
    python_executable: str | None = None,
) -> str:
    """Run one autonomous task and return the agent's final result text."""
    options = build_options(config, config_dir, python_executable=python_executable)
    final = ""
    async for message in query(prompt=task, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    _emit(on_event, block.text.strip())
                elif isinstance(block, ToolUseBlock):
                    _emit(on_event, f"[tool] {block.name} {block.input}")
        elif isinstance(message, ResultMessage):
            final = message.result or final
            if getattr(message, "is_error", False):
                _emit(on_event, f"[error] {getattr(message, 'errors', None) or message.result}")
            cost = getattr(message, "total_cost_usd", None)
            if cost:
                _emit(on_event, f"[cost] ${cost:.4f}  turns={getattr(message, 'num_turns', '?')}")
    return final
