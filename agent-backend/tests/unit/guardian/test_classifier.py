"""Unit tests for the classifier (fake query function, no real Claude)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from agent_backend.guardian.classifier import Classifier
from agent_backend.guardian.config import GuardianConfig


def _config(tmp_path: Path) -> GuardianConfig:
    return GuardianConfig(
        host="127.0.0.1",
        port=2947,
        metrics_port=2948,
        token="s",
        cache_path=str(tmp_path / "c.db"),
        event_log_path=str(tmp_path / "e.jsonl"),
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
