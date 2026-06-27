"""Unit tests for GuardianConfig."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_backend.config import (
    DEFAULT_CODEX_AGENT_MODEL,
    DEFAULT_CODEX_MODEL,
    ConfigError,
)
from agent_backend.guardian.config import (
    DEFAULT_METRICS_PORT,
    DEFAULT_MODEL,
    DEFAULT_PORT,
    DEFAULT_PROFILES_PATH,
    DEFAULT_REQUESTS_PATH,
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


def _codex_env(tmp_path: Path, **over: str) -> dict[str, str]:
    """A signed-in CODEX_HOME (auth.json present) for the codex provider."""
    codex_home = tmp_path / "codex-config"
    codex_home.mkdir(exist_ok=True)
    (codex_home / "auth.json").write_text("{}")
    base = {"AEGIS_AI_PROVIDER": "codex", "CODEX_HOME": str(codex_home)}
    base.update(over)
    return base


def test_token_optional_when_absent() -> None:
    # GUARDIAN_TOKEN is now optional; the "need an auth identity" check lives in
    # load_profiles (a profiles file can supply per-teen tokens instead).
    cfg = GuardianConfig.from_env({"CLAUDE_CODE_OAUTH_TOKEN": "t", "CLAUDE_CONFIG_DIR": "/x"})
    assert cfg.token == ""


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
    assert cfg.requests_path == DEFAULT_REQUESTS_PATH
    assert cfg.parent_pin == ""  # review feature disabled until a PIN is set


def test_overrides() -> None:
    cfg = GuardianConfig.from_env(
        _env(
            GUARDIAN_PORT="9000", GUARDIAN_MODEL="claude-sonnet-4-5", GUARDIAN_ENABLE_VISION="true"
        )
    )
    assert cfg.port == 9000
    assert cfg.model == "claude-sonnet-4-5"
    assert cfg.enable_vision is True


def test_classify_fail_mode_defaults_open() -> None:
    assert GuardianConfig.from_env(_env()).classify_fail_mode == "open"


def test_classify_fail_mode_closed() -> None:
    cfg = GuardianConfig.from_env(_env(GUARDIAN_CLASSIFY_FAIL_MODE="closed"))
    assert cfg.classify_fail_mode == "closed"


def test_classify_fail_mode_rejects_unknown() -> None:
    with pytest.raises(ConfigError, match="GUARDIAN_CLASSIFY_FAIL_MODE"):
        GuardianConfig.from_env(_env(GUARDIAN_CLASSIFY_FAIL_MODE="maybe"))


def test_whitelist_path_override() -> None:
    cfg = GuardianConfig.from_env(_env(GUARDIAN_WHITELIST_PATH="/tmp/wl.json"))
    assert cfg.whitelist_path == "/tmp/wl.json"


def test_requests_path_override() -> None:
    cfg = GuardianConfig.from_env(_env(GUARDIAN_REQUESTS_PATH="/tmp/req.json"))
    assert cfg.requests_path == "/tmp/req.json"


def test_parent_pin_set() -> None:
    cfg = GuardianConfig.from_env(_env(GUARDIAN_PARENT_PIN="1234"))
    assert cfg.parent_pin == "1234"


def test_profiles_path_default() -> None:
    cfg = GuardianConfig.from_env(_env())
    assert cfg.profiles_path == DEFAULT_PROFILES_PATH


def test_profiles_path_override() -> None:
    cfg = GuardianConfig.from_env(_env(GUARDIAN_PROFILES_PATH="/tmp/p.json"))
    assert cfg.profiles_path == "/tmp/p.json"


# --- AI provider selection (claude default, codex via ChatGPT subscription) --


def test_ai_provider_defaults_to_claude() -> None:
    assert GuardianConfig.from_env(_env()).ai_provider == "claude"


def test_ai_provider_invalid_rejected() -> None:
    with pytest.raises(ConfigError, match="AEGIS_AI_PROVIDER"):
        GuardianConfig.from_env(_env(AEGIS_AI_PROVIDER="openai"))


def test_codex_provider_sets_home_and_model_defaults(tmp_path: Path) -> None:
    cfg = GuardianConfig.from_env(_codex_env(tmp_path))
    assert cfg.ai_provider == "codex"
    assert cfg.codex_home.endswith("codex-config")
    assert cfg.model == DEFAULT_CODEX_MODEL
    assert cfg.agent_model == DEFAULT_CODEX_AGENT_MODEL
    # Claude-only auth fields are empty under codex.
    assert cfg.oauth_token == ""
    assert cfg.config_dir == ""


def test_codex_provider_skips_oauth_and_tolerates_api_key(tmp_path: Path) -> None:
    # No CLAUDE_CODE_OAUTH_TOKEN, and an ANTHROPIC_API_KEY present — neither matters for codex.
    cfg = GuardianConfig.from_env(_codex_env(tmp_path, ANTHROPIC_API_KEY="sk-ant"))
    assert cfg.ai_provider == "codex"


def test_codex_provider_requires_codex_home() -> None:
    with pytest.raises(ConfigError, match="CODEX_HOME"):
        GuardianConfig.from_env({"AEGIS_AI_PROVIDER": "codex"})


def test_codex_provider_requires_auth_json(tmp_path: Path) -> None:
    empty = tmp_path / "codex-config"
    empty.mkdir()  # no auth.json inside
    with pytest.raises(ConfigError, match="auth.json"):
        GuardianConfig.from_env({"AEGIS_AI_PROVIDER": "codex", "CODEX_HOME": str(empty)})


def test_codex_model_overrides(tmp_path: Path) -> None:
    cfg = GuardianConfig.from_env(
        _codex_env(tmp_path, GUARDIAN_MODEL="gpt-5", GUARDIAN_AGENT_MODEL="gpt-5-pro")
    )
    assert cfg.model == "gpt-5"
    assert cfg.agent_model == "gpt-5-pro"
