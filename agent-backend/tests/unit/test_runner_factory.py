"""Unit tests for the browser-runner factory."""

from __future__ import annotations

from typing import Any

from agent_backend.config import RunnerConfig
from agent_backend.runner.claude_runner import ClaudeRunner
from agent_backend.runner.codex_runner import CodexRunner
from agent_backend.runner.factory import build_runner


def _config(**over: Any) -> RunnerConfig:
    base: dict[str, Any] = dict(oauth_token="tok", model="m", cdp_url="http://127.0.0.1:9222")
    base.update(over)
    return RunnerConfig(**base)


def test_build_runner_defaults_to_claude() -> None:
    assert isinstance(build_runner(_config(), "/tmp/cfg"), ClaudeRunner)


def test_build_runner_codex() -> None:
    runner = build_runner(_config(ai_provider="codex", codex_home="/tmp/ch"), "/tmp/cfg")
    assert isinstance(runner, CodexRunner)


def test_build_runner_forwards_query_fn() -> None:
    sentinel = object()
    runner = build_runner(_config(), "/tmp/cfg", query_fn=sentinel)
    assert isinstance(runner, ClaudeRunner)
    assert runner._query is sentinel
