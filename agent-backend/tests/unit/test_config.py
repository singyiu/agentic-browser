"""Unit tests for environment-driven configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_backend.config import (
    DEFAULT_CDP_URL,
    DEFAULT_CODEX_AGENT_MODEL,
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

    def test_defaults_to_claude_provider(self) -> None:
        assert RunnerConfig.from_env(self._env()).ai_provider == "claude"


class TestRunnerConfigCodex:
    def _codex_env(self, tmp_path: Path, **over: str) -> dict[str, str]:
        codex_home = tmp_path / "codex-config"
        codex_home.mkdir(exist_ok=True)
        (codex_home / "auth.json").write_text("{}")
        base = {"AEGIS_AI_PROVIDER": "codex", "CODEX_HOME": str(codex_home)}
        base.update(over)
        return base

    def test_codex_provider(self, tmp_path: Path) -> None:
        cfg = RunnerConfig.from_env(self._codex_env(tmp_path))
        assert cfg.ai_provider == "codex"
        assert cfg.oauth_token == ""
        assert cfg.model == DEFAULT_CODEX_AGENT_MODEL
        assert cfg.codex_home.endswith("codex-config")

    def test_codex_requires_auth_json(self, tmp_path: Path) -> None:
        codex_home = tmp_path / "codex-config"
        codex_home.mkdir()  # no auth.json
        with pytest.raises(ConfigError, match="auth.json"):
            RunnerConfig.from_env({"AEGIS_AI_PROVIDER": "codex", "CODEX_HOME": str(codex_home)})

    def test_codex_tolerates_api_key(self, tmp_path: Path) -> None:
        cfg = RunnerConfig.from_env(self._codex_env(tmp_path, ANTHROPIC_API_KEY="sk-ant"))
        assert cfg.ai_provider == "codex"

    def test_invalid_provider_rejected(self) -> None:
        with pytest.raises(ConfigError, match="AEGIS_AI_PROVIDER"):
            RunnerConfig.from_env({"AEGIS_AI_PROVIDER": "bogus"})

    def test_codex_model_override(self, tmp_path: Path) -> None:
        cfg = RunnerConfig.from_env(self._codex_env(tmp_path, AGENT_MODEL="gpt-5"))
        assert cfg.model == "gpt-5"
