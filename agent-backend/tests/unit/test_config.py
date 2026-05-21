"""Unit tests for environment-driven configuration."""

from __future__ import annotations

import pytest

from agent_backend.config import (
    DEFAULT_CDP_URL,
    DEFAULT_MODEL,
    BrowserConfig,
    ConfigError,
    RunnerConfig,
)


class TestBrowserConfig:
    def test_defaults_when_unset(self) -> None:
        cfg = BrowserConfig.from_env({})
        assert cfg.cdp_url == DEFAULT_CDP_URL

    def test_explicit_cdp_url(self) -> None:
        cfg = BrowserConfig.from_env({"CHROMIUM_CDP_URL": "http://localhost:9333"})
        assert cfg.cdp_url == "http://localhost:9333"

    def test_rejects_non_http_url(self) -> None:
        with pytest.raises(ConfigError, match="http"):
            BrowserConfig.from_env({"CHROMIUM_CDP_URL": "ws://localhost:9222"})

    def test_is_frozen(self) -> None:
        cfg = BrowserConfig.from_env({})
        with pytest.raises(AttributeError):
            cfg.cdp_url = "x"  # type: ignore[misc]


class TestRunnerConfig:
    def _env(self, **over: str) -> dict[str, str]:
        base = {"CLAUDE_CODE_OAUTH_TOKEN": "tok-123"}
        base.update(over)
        return base

    def test_requires_oauth_token(self) -> None:
        with pytest.raises(ConfigError, match="CLAUDE_CODE_OAUTH_TOKEN"):
            RunnerConfig.from_env({})

    def test_blank_token_rejected(self) -> None:
        with pytest.raises(ConfigError):
            RunnerConfig.from_env({"CLAUDE_CODE_OAUTH_TOKEN": "   "})

    def test_rejects_api_key_override(self) -> None:
        with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
            RunnerConfig.from_env(self._env(ANTHROPIC_API_KEY="sk-ant-xxx"))

    def test_defaults(self) -> None:
        cfg = RunnerConfig.from_env(self._env())
        assert cfg.oauth_token == "tok-123"
        assert cfg.model == DEFAULT_MODEL
        assert cfg.cdp_url == DEFAULT_CDP_URL

    def test_overrides(self) -> None:
        cfg = RunnerConfig.from_env(
            self._env(AGENT_MODEL="claude-opus-4", CHROMIUM_CDP_URL="http://127.0.0.1:9999")
        )
        assert cfg.model == "claude-opus-4"
        assert cfg.cdp_url == "http://127.0.0.1:9999"
