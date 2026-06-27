"""Claude Max subscription runner (browser-control MCP via claude-agent-sdk).

Drives the autonomous loop with the user's Claude Max subscription
(``CLAUDE_CODE_OAUTH_TOKEN``), restricted to the browser-control MCP tools, with
config/skills isolated via ``CLAUDE_CONFIG_DIR``.
"""

from __future__ import annotations

import sys
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

from ..config import RunnerConfig
from .base import (
    _DISALLOWED,
    SERVER_NAME,
    SYSTEM_PROMPT,
    EventSink,
    allowed_tools,
    emit,
)


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


class ClaudeRunner:
    """``BrowserAgentRunner`` backed by the Claude Agent SDK.

    ``query_fn`` is injectable for tests (defaults to ``claude_agent_sdk.query``).
    """

    def __init__(
        self,
        config: RunnerConfig,
        config_dir: str,
        *,
        query_fn: Any = None,
        python_executable: str | None = None,
    ) -> None:
        self._config = config
        self._config_dir = config_dir
        self._query = query_fn if query_fn is not None else query
        self._python = python_executable

    async def run_task(self, task: str, *, on_event: EventSink | None = None) -> str:
        """Run one autonomous task and return the agent's final result text."""
        options = build_options(self._config, self._config_dir, python_executable=self._python)
        final = ""
        async for message in self._query(prompt=task, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        emit(on_event, block.text.strip())
                    elif isinstance(block, ToolUseBlock):
                        emit(on_event, f"[tool] {block.name} {block.input}")
            elif isinstance(message, ResultMessage):
                final = message.result or final
                if getattr(message, "is_error", False):
                    emit(on_event, f"[error] {getattr(message, 'errors', None) or message.result}")
                cost = getattr(message, "total_cost_usd", None)
                if cost:
                    emit(
                        on_event, f"[cost] ${cost:.4f}  turns={getattr(message, 'num_turns', '?')}"
                    )
        return final
