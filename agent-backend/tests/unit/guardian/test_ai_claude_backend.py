"""Unit tests for ClaudeBackend (fake query_fn; no real Claude)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from agent_backend.guardian.ai.backends.claude import ClaudeBackend
from agent_backend.guardian.config import GuardianConfig


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
        parent_pin="pin",
        classify_timeout_s=5.0,
        screenshot_confidence_threshold=0.6,
        enable_vision=False,
        model="claude-haiku-4-5",
        config_dir=str(tmp_path),
        oauth_token="tok",
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


async def test_complete_collects_assistant_text(tmp_path: Path) -> None:
    async def fake_query(*, prompt: str, options: object) -> AsyncIterator[object]:
        yield _assistant("hello ")
        yield _assistant("world")
        yield _result()

    out = await ClaudeBackend(_config(tmp_path), query_fn=fake_query).complete(
        system_prompt="s", user_prompt="u", model=None
    )
    assert out == "hello world"


async def test_complete_prefers_structured_output(tmp_path: Path) -> None:
    async def fake_query(*, prompt: str, options: object) -> AsyncIterator[object]:
        yield _result(structured={"verdict": "allow", "confidence": 0.9})

    out = await ClaudeBackend(_config(tmp_path), query_fn=fake_query).complete(
        system_prompt="s", user_prompt="u", model=None
    )
    assert json.loads(out) == {"verdict": "allow", "confidence": 0.9}


async def test_complete_sets_system_and_user_prompt(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def fake_query(*, prompt: str, options: object) -> AsyncIterator[object]:
        captured["system_prompt"] = options.system_prompt  # type: ignore[attr-defined]
        captured["prompt"] = prompt
        yield _result(result="ok")

    await ClaudeBackend(_config(tmp_path), query_fn=fake_query).complete(
        system_prompt="SYS-X", user_prompt="USER-Y", model=None
    )
    assert captured["system_prompt"] == "SYS-X"
    assert captured["prompt"] == "USER-Y"


async def test_complete_model_override_then_default(tmp_path: Path) -> None:
    seen: list[object] = []

    async def fake_query(*, prompt: str, options: object) -> AsyncIterator[object]:
        seen.append(options.model)  # type: ignore[attr-defined]
        yield _result(result="ok")

    backend = ClaudeBackend(_config(tmp_path), query_fn=fake_query)
    await backend.complete(system_prompt="s", user_prompt="u", model="override-m")
    await backend.complete(system_prompt="s", user_prompt="u", model=None)
    assert seen == ["override-m", "claude-haiku-4-5"]


async def test_complete_propagates_exception(tmp_path: Path) -> None:
    async def boom(*, prompt: str, options: object) -> AsyncIterator[object]:
        raise RuntimeError("transport boom")
        yield  # pragma: no cover - makes this an async generator

    with pytest.raises(RuntimeError, match="transport boom"):
        await ClaudeBackend(_config(tmp_path), query_fn=boom).complete(
            system_prompt="s", user_prompt="u", model=None
        )
