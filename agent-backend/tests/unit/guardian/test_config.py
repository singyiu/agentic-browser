"""Unit tests for GuardianConfig."""

from __future__ import annotations

import pytest

from agent_backend.config import ConfigError
from agent_backend.guardian.config import (
    DEFAULT_METRICS_PORT,
    DEFAULT_MODEL,
    DEFAULT_PORT,
    DEFAULT_WHITELIST_PATH,
    GuardianConfig,
)


def _env(**over: str) -> dict[str, str]:
    base = {
        "GUARDIAN_TOKEN": "sec",
        "CLAUDE_CODE_OAUTH_TOKEN": "tok",
        "CLAUDE_CONFIG_DIR": "/tmp/cfg",
    }
    base.update(over)
    return base


def test_requires_guardian_token() -> None:
    with pytest.raises(ConfigError, match="GUARDIAN_TOKEN"):
        GuardianConfig.from_env({"CLAUDE_CODE_OAUTH_TOKEN": "t", "CLAUDE_CONFIG_DIR": "/x"})


def test_requires_oauth_token() -> None:
    with pytest.raises(ConfigError, match="CLAUDE_CODE_OAUTH_TOKEN"):
        GuardianConfig.from_env({"GUARDIAN_TOKEN": "s", "CLAUDE_CONFIG_DIR": "/x"})


def test_rejects_api_key() -> None:
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        GuardianConfig.from_env(_env(ANTHROPIC_API_KEY="sk-ant"))


def test_requires_config_dir() -> None:
    with pytest.raises(ConfigError, match="CLAUDE_CONFIG_DIR"):
        GuardianConfig.from_env({"GUARDIAN_TOKEN": "s", "CLAUDE_CODE_OAUTH_TOKEN": "t"})


def test_defaults() -> None:
    cfg = GuardianConfig.from_env(_env())
    assert cfg.port == DEFAULT_PORT
    assert cfg.model == DEFAULT_MODEL
    assert cfg.enable_vision is False
    assert cfg.classify_timeout_s == 180.0
    assert cfg.metrics_port == DEFAULT_METRICS_PORT
    assert cfg.whitelist_path == DEFAULT_WHITELIST_PATH


def test_overrides() -> None:
    cfg = GuardianConfig.from_env(
        _env(
            GUARDIAN_PORT="9000", GUARDIAN_MODEL="claude-sonnet-4-5", GUARDIAN_ENABLE_VISION="true"
        )
    )
    assert cfg.port == 9000
    assert cfg.model == "claude-sonnet-4-5"
    assert cfg.enable_vision is True


def test_whitelist_path_override() -> None:
    cfg = GuardianConfig.from_env(_env(GUARDIAN_WHITELIST_PATH="/tmp/wl.json"))
    assert cfg.whitelist_path == "/tmp/wl.json"
