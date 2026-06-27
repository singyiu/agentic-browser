"""Unit tests for the completion-backend factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from agent_backend.guardian.ai.backend import CompletionBackend
from agent_backend.guardian.ai.backends.claude import ClaudeBackend
from agent_backend.guardian.ai.backends.codex import CodexBackend
from agent_backend.guardian.ai.factory import build_backend
from agent_backend.guardian.config import GuardianConfig


def _config(tmp_path: Path, **over: Any) -> GuardianConfig:
    base: dict[str, Any] = dict(
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
        model="m",
        config_dir=str(tmp_path),
        oauth_token="tok",
    )
    base.update(over)
    return GuardianConfig(**base)


def test_build_backend_defaults_to_claude(tmp_path: Path) -> None:
    backend = build_backend(_config(tmp_path))
    assert isinstance(backend, ClaudeBackend)
    assert isinstance(backend, CompletionBackend)


def test_build_backend_codex(tmp_path: Path) -> None:
    backend = build_backend(_config(tmp_path, ai_provider="codex", codex_home=str(tmp_path)))
    assert isinstance(backend, CodexBackend)
    assert isinstance(backend, CompletionBackend)


def test_build_backend_forwards_query_fn(tmp_path: Path) -> None:
    async def fake_query(*, prompt: str, options: object) -> AsyncIterator[object]:
        yield  # pragma: no cover

    backend = build_backend(_config(tmp_path), query_fn=fake_query)
    assert isinstance(backend, ClaudeBackend)
    assert backend._query is fake_query
