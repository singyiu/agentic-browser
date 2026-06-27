"""Headless content classifier (provider-agnostic).

Each classification is an independent, stateless one-shot completion so page verdicts never
bleed into each other and context can't grow unbounded. The rubric (single source of truth) is
defined in rubric.py. Always fail-open: any error yields ``allow``.

Prompt assembly and verdict parsing live here; the model call is delegated to a
``CompletionBackend`` (Claude Max subscription or Codex / ChatGPT subscription), selected from
``config.ai_provider`` by :func:`agent_backend.guardian.ai.factory.build_backend`.
"""

from __future__ import annotations

from typing import Any

from .ai.backend import CompletionBackend
from .config import DEFAULT_AGE, GuardianConfig
from .rubric import rubric
from .verdict import Verdict, allow, parse_verdict

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
    """Provider-agnostic classifier: assembles prompts, delegates the model call to a backend.

    ``backend`` may be injected directly (tests). Otherwise it is built from
    ``config.ai_provider``; ``query_fn`` is the Claude backend's fake-query test seam and is
    only meaningful when the provider is ``claude``.
    """

    def __init__(
        self,
        config: GuardianConfig,
        *,
        query_fn: Any = None,
        backend: CompletionBackend | None = None,
    ) -> None:
        self._config = config
        if backend is not None:
            self._backend = backend
        else:
            from .ai.factory import build_backend

            self._backend = build_backend(config, query_fn=query_fn)

    def build_prompt(self, payload: dict[str, str]) -> str:
        body = str(payload.get("body_snippet", ""))[:2000]
        return (
            f"URL: {payload.get('url', '')}\n"
            f"Title: {payload.get('title', '')}\n"
            f"Description: {payload.get('meta_desc', '') or payload.get('og_desc', '')}\n"
            f"OG title: {payload.get('og_title', '')}\n"
            f"Body snippet:\n{body}"
        )

    def _classify_system_prompt(
        self,
        *,
        age: int,
        policy: str,
        approved_topics: tuple[str, ...],
        disallowed_topics: tuple[str, ...],
    ) -> str:
        """Assemble the page-classification system prompt (provider-agnostic)."""
        return (
            _instructions(age)
            + rubric(age)
            + policy
            + _approved_block(approved_topics)
            + _disallowed_block(disallowed_topics)
        )

    async def generate(
        self, *, system_prompt: str, user_prompt: str, model: str | None = None
    ) -> str:
        """Run one stateless prose generation (no rubric/policy/topic injection).

        Returns the collected text. Unlike ``classify`` this does NOT fail open: backend errors
        propagate so the caller (the suggest-block-rule endpoint) can surface them as a 502.
        ``model`` overrides the configured classifier model (the Agent chat uses a stronger one).
        """
        return await self._backend.complete(
            system_prompt=system_prompt, user_prompt=user_prompt, model=model
        )

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
        system_prompt = self._classify_system_prompt(
            age=age,
            policy=policy,
            approved_topics=approved_topics,
            disallowed_topics=disallowed_topics,
        )
        try:
            collected = await self._backend.complete(
                system_prompt=system_prompt,
                user_prompt=self.build_prompt(payload),
                model=None,
            )
        except Exception as exc:  # noqa: BLE001 - fail-open on any backend error
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
            collected = await self._backend.complete(
                system_prompt=_search_instructions(age) + policy,
                user_prompt=f"Search query: {query[:500]}",
                model=None,
            )
        except Exception as exc:  # noqa: BLE001 - fail-open on any backend error
            return allow(f"classifier_error: {type(exc).__name__}")
        verdict = parse_verdict(collected)
        if verdict.verdict == "block":
            return verdict
        return allow(verdict.reason or "search_allow", verdict.confidence)
