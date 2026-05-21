"""Headless Claude classifier (Claude Max subscription via claude-agent-sdk).

Each classification is an independent, stateless one-shot ``query()`` (the proven
runner pattern) so page verdicts never bleed into each other and context can't grow
unbounded. The rubric (single source of truth) is defined in rubric.py.
Always fail-open: any error yields ``allow``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query

from .config import GuardianConfig
from .rubric import RUBRIC
from .verdict import Verdict, allow, parse_verdict

_DISALLOWED = ["Bash", "Edit", "Write", "Read", "NotebookEdit", "WebFetch", "WebSearch", "Task"]

_INSTRUCTIONS = (
    "You are a parental-control content classifier protecting a 10-year-old child. "
    "Apply the POLICY below to the page. Respond with ONLY a JSON object "
    "(no prose, no code fences):\n"
    '{"verdict":"allow"|"block","reason":"<one child-friendly sentence>",'
    '"confidence":<0..1>,"categories":["..."]}\n'
    "If the text alone is insufficient to judge (image-heavy or ambiguous), respond "
    '{"verdict":"need_screenshot","reason":"...","confidence":<0..1>,"categories":[]}.\n'
    'When uncertain whether to block, prefer "allow" with confidence below 0.6.\n\nPOLICY:\n'
)


class Classifier:
    def __init__(self, config: GuardianConfig, *, query_fn: Any = query) -> None:
        self._config = config
        self._query = query_fn
        self._rubric = RUBRIC
        self._lock = asyncio.Lock()

    def _options(self) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            model=self._config.model,
            system_prompt=_INSTRUCTIONS + self._rubric,
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

    def build_prompt(self, payload: dict[str, str]) -> str:
        body = str(payload.get("body_snippet", ""))[:2000]
        return (
            f"URL: {payload.get('url', '')}\n"
            f"Title: {payload.get('title', '')}\n"
            f"Description: {payload.get('meta_desc', '') or payload.get('og_desc', '')}\n"
            f"OG title: {payload.get('og_title', '')}\n"
            f"Body snippet:\n{body}"
        )

    async def classify(
        self, payload: dict[str, str], *, screenshot_b64: str | None = None
    ) -> Verdict:
        # Vision is gated behind a verified-off flag; until confirmed, screenshots are ignored
        # and the page falls back to text classification (the service then fails open).
        prompt = self.build_prompt(payload)
        collected = ""
        try:
            async with self._lock:
                async for message in self._query(prompt=prompt, options=self._options()):
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
        except Exception as exc:  # noqa: BLE001 - fail-open on any SDK/transport error
            return allow(f"classifier_error: {type(exc).__name__}")
        return parse_verdict(collected)
