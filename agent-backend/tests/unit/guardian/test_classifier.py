"""Unit tests for the classifier (fake query function, no real Claude)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from agent_backend.guardian.classifier import Classifier
from agent_backend.guardian.config import GuardianConfig
from agent_backend.guardian.verdict import Verdict


def _config(tmp_path: Path) -> GuardianConfig:
    return GuardianConfig(
        host="127.0.0.1",
        port=2947,
        metrics_port=2948,
        token="s",
        cache_path=str(tmp_path / "c.db"),
        event_log_path=str(tmp_path / "e.jsonl"),
        whitelist_path=str(tmp_path / "wl.json"),
        blocklist_path=str(tmp_path / "bl.json"),
        requests_path=str(tmp_path / "req.json"),
        parent_pin="testpin",
        classify_timeout_s=5.0,
        screenshot_confidence_threshold=0.6,
        enable_vision=False,
        model="m",
        config_dir=str(tmp_path),
        oauth_token="t",
    )


def _assistant(text: str) -> AssistantMessage:
    msg = object.__new__(AssistantMessage)
    msg.content = [TextBlock(text=text)]  # type: ignore[attr-defined]
    return msg


def _result(result: str = "", structured: object = None) -> ResultMessage:
    msg = object.__new__(ResultMessage)
    msg.result = result  # type: ignore[attr-defined]
    msg.structured_output = structured  # type: ignore[attr-defined]
    return msg


async def test_classify_parses_block(tmp_path: Path) -> None:
    async def fake_query(*, prompt: str, options: object) -> AsyncIterator[object]:
        yield _assistant(
            '{"verdict":"block","reason":"bad","confidence":0.9,"categories":["violence"]}'
        )
        yield _result()

    verdict = await Classifier(_config(tmp_path), query_fn=fake_query).classify(
        {"url": "u", "title": "t"}
    )
    assert verdict.verdict == "block"
    assert verdict.confidence == 0.9


async def test_classify_uses_structured_output(tmp_path: Path) -> None:
    async def fake_query(*, prompt: str, options: object) -> AsyncIterator[object]:
        yield _result(structured={"verdict": "allow", "confidence": 0.95})

    verdict = await Classifier(_config(tmp_path), query_fn=fake_query).classify({"url": "u"})
    assert verdict.verdict == "allow"
    assert verdict.confidence == 0.95


async def test_classify_fails_open_on_error(tmp_path: Path) -> None:
    async def fake_query(*, prompt: str, options: object) -> AsyncIterator[object]:
        raise RuntimeError("transport boom")
        yield  # pragma: no cover - makes this an async generator

    verdict = await Classifier(_config(tmp_path), query_fn=fake_query).classify({"url": "u"})
    assert verdict.verdict == "allow"


def test_build_prompt_truncates_body(tmp_path: Path) -> None:
    classifier = Classifier(_config(tmp_path), query_fn=lambda **_: None)
    prompt = classifier.build_prompt({"url": "u", "body_snippet": "x" * 5000})
    assert len(prompt) < 2300


async def test_approved_topics_injected_into_system_prompt(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def fake_query(*, prompt: str, options: object) -> AsyncIterator[object]:
        captured["system_prompt"] = options.system_prompt  # type: ignore[attr-defined]
        yield _result(structured={"verdict": "allow", "confidence": 0.9})

    await Classifier(_config(tmp_path), query_fn=fake_query).classify(
        {"url": "u"}, approved_topics=("BeyBlade anime",)
    )
    system_prompt = captured["system_prompt"]
    assert isinstance(system_prompt, str)
    assert "PARENT-APPROVED" in system_prompt
    assert "BeyBlade anime" in system_prompt


async def test_no_approved_topics_omits_block(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def fake_query(*, prompt: str, options: object) -> AsyncIterator[object]:
        captured["system_prompt"] = options.system_prompt  # type: ignore[attr-defined]
        yield _result(structured={"verdict": "allow", "confidence": 0.9})

    await Classifier(_config(tmp_path), query_fn=fake_query).classify({"url": "u"})
    system_prompt = captured["system_prompt"]
    assert isinstance(system_prompt, str)
    assert "PARENT-APPROVED" not in system_prompt


async def test_disallowed_topics_injected_into_system_prompt(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def fake_query(*, prompt: str, options: object) -> AsyncIterator[object]:
        captured["system_prompt"] = options.system_prompt  # type: ignore[attr-defined]
        yield _result(structured={"verdict": "allow", "confidence": 0.9})

    await Classifier(_config(tmp_path), query_fn=fake_query).classify(
        {"url": "u"}, disallowed_topics=("online gambling",)
    )
    system_prompt = captured["system_prompt"]
    assert isinstance(system_prompt, str)
    assert "PARENT-BLOCKED" in system_prompt
    assert "online gambling" in system_prompt


async def test_no_disallowed_topics_omits_block(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def fake_query(*, prompt: str, options: object) -> AsyncIterator[object]:
        captured["system_prompt"] = options.system_prompt  # type: ignore[attr-defined]
        yield _result(structured={"verdict": "allow", "confidence": 0.9})

    await Classifier(_config(tmp_path), query_fn=fake_query).classify({"url": "u"})
    system_prompt = captured["system_prompt"]
    assert isinstance(system_prompt, str)
    assert "PARENT-BLOCKED" not in system_prompt


# --- age parameterization + merged household policy ------------------------


async def _system_prompt_for(tmp_path: Path, **classify_kwargs: object) -> str:
    """Run one classify and return the system prompt the fake query saw."""
    captured: dict[str, object] = {}

    async def fake_query(*, prompt: str, options: object) -> AsyncIterator[object]:
        captured["system_prompt"] = options.system_prompt  # type: ignore[attr-defined]
        yield _result(structured={"verdict": "allow", "confidence": 0.9})

    await Classifier(_config(tmp_path), query_fn=fake_query).classify(
        {"url": "u"}, **classify_kwargs
    )
    system_prompt = captured["system_prompt"]
    assert isinstance(system_prompt, str)
    return system_prompt


async def test_age_parameterizes_instructions_and_rubric(tmp_path: Path) -> None:
    # A teen's configured age must reach BOTH the instructions and the rubric wording,
    # replacing the built-in default of 10.
    system_prompt = await _system_prompt_for(tmp_path, age=14)
    assert "14-year-old" in system_prompt
    assert "10-year-old" not in system_prompt


async def test_default_age_is_ten(tmp_path: Path) -> None:
    # No age supplied => the historical age-10 wording, unchanged.
    assert "10-year-old" in await _system_prompt_for(tmp_path)


async def test_age_substitution_preserves_json_braces(tmp_path: Path) -> None:
    # The instructions embed a literal JSON example with braces; the age substitution
    # must not corrupt it (i.e. it uses str.replace, never str.format).
    system_prompt = await _system_prompt_for(tmp_path, age=8)
    assert '{"verdict":"allow"|"block"' in system_prompt
    assert "8-year-old" in system_prompt


async def test_policy_rendered_after_rubric_before_approved(tmp_path: Path) -> None:
    # The merged household policy sits between the rubric body and the parent-approved block.
    system_prompt = await _system_prompt_for(
        tmp_path,
        policy="\n\nHOUSEHOLD GUIDANCE: no anonymous chat rooms.",
        approved_topics=("BeyBlade anime",),
    )
    assert "HOUSEHOLD GUIDANCE: no anonymous chat rooms." in system_prompt
    assert system_prompt.index("NEVER BLOCK") < system_prompt.index("HOUSEHOLD GUIDANCE")
    assert system_prompt.index("HOUSEHOLD GUIDANCE") < system_prompt.index("PARENT-APPROVED")


async def test_default_prompt_is_instructions_plus_rubric_only(tmp_path: Path) -> None:
    # Regression guard: with no age/policy/topics, the prompt is byte-identical to the
    # base instructions + rubric (no household, approved, or blocked sections appended).
    from agent_backend.guardian.classifier import _instructions
    from agent_backend.guardian.rubric import rubric

    assert await _system_prompt_for(tmp_path) == _instructions(10) + rubric(10)


# --- classify_search_query (bare search-query safety filter) ----------------


async def _search_query_with(tmp_path: Path, response: str, **kwargs: object) -> Verdict:
    """Run classify_search_query against a fake one-shot query that returns ``response``."""

    async def fake_query(*, prompt: str, options: object) -> AsyncIterator[object]:
        yield _assistant(response)
        yield _result()

    return await Classifier(_config(tmp_path), query_fn=fake_query).classify_search_query(
        "some query", **kwargs  # type: ignore[arg-type]
    )


async def test_search_parses_block(tmp_path: Path) -> None:
    verdict = await _search_query_with(
        tmp_path, '{"verdict":"block","reason":"adult","confidence":0.95}'
    )
    assert verdict.verdict == "block"


async def test_search_parses_allow(tmp_path: Path) -> None:
    verdict = await _search_query_with(tmp_path, '{"verdict":"allow","reason":"fine"}')
    assert verdict.verdict == "allow"


async def test_search_need_screenshot_coerced_to_allow(tmp_path: Path) -> None:
    # A bare query has nothing to screenshot; any non-block verdict must become allow.
    verdict = await _search_query_with(tmp_path, '{"verdict":"need_screenshot","reason":"x"}')
    assert verdict.verdict == "allow"


async def test_search_fails_open_on_error(tmp_path: Path) -> None:
    async def boom(*, prompt: str, options: object) -> AsyncIterator[object]:
        raise RuntimeError("transport boom")
        yield  # pragma: no cover - makes this an async generator

    verdict = await Classifier(_config(tmp_path), query_fn=boom).classify_search_query("q")
    assert verdict.verdict == "allow"


async def test_search_system_prompt_has_age_and_policy(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def fake_query(*, prompt: str, options: object) -> AsyncIterator[object]:
        captured["system_prompt"] = options.system_prompt  # type: ignore[attr-defined]
        captured["prompt"] = prompt
        yield _result(structured={"verdict": "allow"})

    await Classifier(_config(tmp_path), query_fn=fake_query).classify_search_query(
        "minecraft", age=9, policy="\n\nHOUSEHOLD: be strict."
    )
    system_prompt = captured["system_prompt"]
    assert isinstance(system_prompt, str)
    assert "9-year-old" in system_prompt
    assert "search filter" in system_prompt
    assert "HOUSEHOLD: be strict." in system_prompt
    assert '{"verdict":"allow"|"block"' in system_prompt  # JSON braces survive substitution
    assert captured["prompt"] == "Search query: minecraft"


# --- generate (one-shot prose, e.g. a suggested blocking rule) --------------


async def test_generate_collects_text(tmp_path: Path) -> None:
    async def fake_query(*, prompt: str, options: object) -> AsyncIterator[object]:
        yield _assistant("Block websites showing pornographic material.")
        yield _result()

    out = await Classifier(_config(tmp_path), query_fn=fake_query).generate(
        system_prompt="sys", user_prompt="user"
    )
    assert out == "Block websites showing pornographic material."


async def test_generate_passes_system_and_user_prompt(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def fake_query(*, prompt: str, options: object) -> AsyncIterator[object]:
        captured["system_prompt"] = options.system_prompt  # type: ignore[attr-defined]
        captured["prompt"] = prompt
        yield _assistant("ok")
        yield _result()

    await Classifier(_config(tmp_path), query_fn=fake_query).generate(
        system_prompt="SYSTEM-X", user_prompt="USER-Y"
    )
    assert captured["system_prompt"] == "SYSTEM-X"
    assert captured["prompt"] == "USER-Y"


async def test_generate_propagates_errors(tmp_path: Path) -> None:
    # Unlike classify (which fails open), generate surfaces transport errors so the
    # caller can decide; the suggest endpoint turns this into a 502.
    async def boom(*, prompt: str, options: object) -> AsyncIterator[object]:
        raise RuntimeError("transport boom")
        yield  # pragma: no cover - makes this an async generator

    with pytest.raises(RuntimeError):
        await Classifier(_config(tmp_path), query_fn=boom).generate(
            system_prompt="s", user_prompt="u"
        )


# --- explicit backend injection (provider-agnostic seam) --------------------


async def test_classify_with_injected_backend(tmp_path: Path) -> None:
    # A backend may be injected directly, bypassing provider selection entirely.
    class _Backend:
        async def complete(self, *, system_prompt: str, user_prompt: str, model: object) -> str:
            return '{"verdict":"block","reason":"x","confidence":0.9,"categories":[]}'

    verdict = await Classifier(_config(tmp_path), backend=_Backend()).classify({"url": "u"})
    assert verdict.verdict == "block"


async def test_generate_with_injected_backend(tmp_path: Path) -> None:
    class _Backend:
        async def complete(self, *, system_prompt: str, user_prompt: str, model: object) -> str:
            return "generated text"

    out = await Classifier(_config(tmp_path), backend=_Backend()).generate(
        system_prompt="s", user_prompt="u"
    )
    assert out == "generated text"
