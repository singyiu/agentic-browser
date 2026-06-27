"""Claude Max subscription backend (headless ``claude`` CLI via claude-agent-sdk).

Each completion is an independent, stateless one-shot ``query()`` so calls never bleed into
each other and context can't grow unbounded. A lock serializes calls: one ``claude`` subprocess
at a time. This module is imported only when the active provider is ``claude``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)

from ...config import GuardianConfig

# Built-in tools the classifier must never touch — it is a pure text-in/text-out judge.
_DISALLOWED = ["Bash", "Edit", "Write", "Read", "NotebookEdit", "WebFetch", "WebSearch", "Task"]


class ClaudeBackend:
    """``CompletionBackend`` backed by the Claude Agent SDK.

    ``query_fn`` is injectable for tests (defaults to ``claude_agent_sdk.query``); it is called
    as ``query_fn(prompt=user_prompt, options=ClaudeAgentOptions(...))`` and must yield SDK
    messages (``AssistantMessage`` / ``ResultMessage``).
    """

    def __init__(self, config: GuardianConfig, *, query_fn: Any = None) -> None:
        self._config = config
        self._query = query_fn if query_fn is not None else query
        self._lock = asyncio.Lock()

    def _options(self, system_prompt: str, *, model: str | None = None) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            model=model or self._config.model,
            system_prompt=system_prompt,
            allowed_tools=[],
            disallowed_tools=list(_DISALLOWED),
            permission_mode="bypassPermissions",
            setting_sources=[],
            mcp_servers={},
            env={
                "CLAUDE_CONFIG_DIR": self._config.config_dir,
                "CLAUDE_CODE_OAUTH_TOKEN": self._config.oauth_token,
            },
        )

    async def complete(self, *, system_prompt: str, user_prompt: str, model: str | None) -> str:
        """Run one stateless query; return the collected text (structured output preferred)."""
        options = self._options(system_prompt, model=model)
        collected = ""
        async with self._lock:
            async for message in self._query(prompt=user_prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            collected += block.text
                elif isinstance(message, ResultMessage):
                    structured = getattr(message, "structured_output", None)
                    if isinstance(structured, dict) and structured:
                        collected = json.dumps(structured)
                    elif message.result:
                        collected = message.result
        return collected
