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

from .config import DEFAULT_AGE, GuardianConfig
from .rubric import rubric
from .verdict import Verdict, allow, parse_verdict

_DISALLOWED = ["Bash", "Edit", "Write", "Read", "NotebookEdit", "WebFetch", "WebSearch", "Task"]

_INSTRUCTIONS = (
    "You are a parental-control content classifier protecting a {age}-year-old child. "
    "Apply the POLICY below to the page. Respond with ONLY a JSON object "
    "(no prose, no code fences):\n"
    '{"verdict":"allow"|"block","reason":"<one child-friendly sentence>",'
    '"confidence":<0..1>,"categories":["..."]}\n'
    "If the text alone is insufficient to judge (image-heavy or ambiguous), respond "
    '{"verdict":"need_screenshot","reason":"...","confidence":<0..1>,"categories":[]}.\n'
    'When uncertain whether to block, prefer "allow" with confidence below 0.6.\n\nPOLICY:\n'
)


def _instructions(age: int) -> str:
    """Render the instruction preamble for a specific child ``age``.

    Uses ``str.replace`` (never ``str.format``) because the template embeds a literal JSON
    example whose braces would otherwise break format-string parsing.
    """
    return _INSTRUCTIONS.replace("{age}", str(age))


# Hard-block categories that a parent-approved topic must never override (safety floor).
_ALWAYS_BLOCK = "adult_content, graphic_violence, self_harm, hate, or illegal_dangerous"

# Search-query safety filter: judges a bare query (no page) for age-appropriateness. The parent
# keyword lists handle explicit allow/block; this catches obviously-unsafe queries the parent
# never listed. {age} is substituted via str.replace (the literal JSON braces forbid str.format).
_SEARCH_INSTRUCTIONS = (
    "You are a parental-control search filter protecting a {age}-year-old child. "
    "Decide whether it is safe for this child to run the search query below. "
    "Respond with ONLY a JSON object (no prose, no code fences):\n"
    '{"verdict":"allow"|"block","reason":"<one child-friendly sentence>",'
    '"confidence":<0..1>,"categories":["..."]}\n'
    "Always block a query seeking " + _ALWAYS_BLOCK + " content, regardless of any "
    'household guidance below. When uncertain, prefer "allow".\n\nHOUSEHOLD GUIDANCE:\n'
)


def _search_instructions(age: int) -> str:
    """Render the search-filter preamble for ``age`` (str.replace, never str.format)."""
    return _SEARCH_INSTRUCTIONS.replace("{age}", str(age))


def _approved_block(topics: tuple[str, ...]) -> str:
    """Render the parent-approved-topics section appended to the system prompt.

    Topics are injected VERBATIM, so only parent-controlled input (the token-authed
    whitelist) must ever reach ``approved_topics`` — never page content or child input.
    Empty topics -> empty string, so the prompt is byte-identical to the default.
    """
    if not topics:
        return ""
    listed = "\n".join(f"- {topic}" for topic in topics)
    return (
        "\n\nPARENT-APPROVED TOPICS:\n"
        "The parent has explicitly approved the following topics for this child. If the page "
        'is clearly about one of these, return "allow" with high confidence — EXCEPT always '
        f"block {_ALWAYS_BLOCK} regardless:\n{listed}"
    )


def _disallowed_block(topics: tuple[str, ...]) -> str:
    """Render the parent-blocked-topics section appended to the system prompt.

    Symmetric to ``_approved_block``: topics are injected VERBATIM, so only parent-controlled
    input (the PIN-gated blocklist) must ever reach ``disallowed_topics``. Empty -> empty string.
    """
    if not topics:
        return ""
    listed = "\n".join(f"- {topic}" for topic in topics)
    return (
        "\n\nPARENT-BLOCKED TOPICS:\n"
        "The parent has explicitly disallowed the following topics for this child. If the page "
        'is clearly about one of these, return "block" with high confidence:\n' + listed
    )


class Classifier:
    def __init__(self, config: GuardianConfig, *, query_fn: Any = query) -> None:
        self._config = config
        self._query = query_fn
        self._lock = asyncio.Lock()

    def _base_options(self, system_prompt: str) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            model=self._config.model,
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

    def _options(
        self,
        *,
        age: int = DEFAULT_AGE,
        policy: str = "",
        approved_topics: tuple[str, ...] = (),
        disallowed_topics: tuple[str, ...] = (),
    ) -> ClaudeAgentOptions:
        return self._base_options(
            _instructions(age)
            + rubric(age)
            + policy
            + _approved_block(approved_topics)
            + _disallowed_block(disallowed_topics)
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

    async def _run(self, prompt: str, options: ClaudeAgentOptions) -> str:
        """Run one stateless query; return the collected text (structured output preferred)."""
        collected = ""
        async with self._lock:
            async for message in self._query(prompt=prompt, options=options):
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

    async def classify(
        self,
        payload: dict[str, str],
        *,
        screenshot_b64: str | None = None,
        age: int = DEFAULT_AGE,
        policy: str = "",
        approved_topics: tuple[str, ...] = (),
        disallowed_topics: tuple[str, ...] = (),
    ) -> Verdict:
        # Vision is gated behind a verified-off flag; until confirmed, screenshots are ignored
        # and the page falls back to text classification (the service then fails open).
        options = self._options(
            age=age,
            policy=policy,
            approved_topics=approved_topics,
            disallowed_topics=disallowed_topics,
        )
        try:
            collected = await self._run(self.build_prompt(payload), options)
        except Exception as exc:  # noqa: BLE001 - fail-open on any SDK/transport error
            return allow(f"classifier_error: {type(exc).__name__}")
        return parse_verdict(collected)

    async def classify_search_query(
        self, query: str, *, age: int = DEFAULT_AGE, policy: str = ""
    ) -> Verdict:
        """Judge a bare search query for age-appropriateness. Fail-open (allow) on any error.

        Only a ``block`` verdict blocks; any other verdict (incl. ``need_screenshot``) becomes
        ``allow``, since a bare query carries no page or image to escalate to.
        """
        try:
            collected = await self._run(
                f"Search query: {query[:500]}",
                self._base_options(_search_instructions(age) + policy),
            )
        except Exception as exc:  # noqa: BLE001 - fail-open on any SDK/transport error
            return allow(f"classifier_error: {type(exc).__name__}")
        verdict = parse_verdict(collected)
        if verdict.verdict == "block":
            return verdict
        return allow(verdict.reason or "search_allow", verdict.confidence)
