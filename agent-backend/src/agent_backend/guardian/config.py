"""Validated configuration for the guardian service."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from ..config import ConfigError

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 2947
DEFAULT_MODEL = "claude-haiku-4-5"  # fast + cheap for per-page classification
DEFAULT_TIMEOUT_S = 180.0  # a classification spawns a `claude` subprocess; give it room
DEFAULT_SCREENSHOT_THRESHOLD = 0.6
DEFAULT_CACHE_PATH = "data/guardian_cache.db"
DEFAULT_EVENT_LOG_PATH = "data/guardian_events.jsonl"


def _clean(value: str | None) -> str:
    return (value or "").strip()


@dataclass(frozen=True, slots=True)
class GuardianConfig:
    host: str
    port: int
    token: str
    cache_path: str
    event_log_path: str
    classify_timeout_s: float
    screenshot_confidence_threshold: float
    enable_vision: bool
    model: str
    config_dir: str
    oauth_token: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> GuardianConfig:
        e = os.environ if env is None else env
        token = _clean(e.get("GUARDIAN_TOKEN"))
        if not token:
            raise ConfigError(
                "GUARDIAN_TOKEN is not set (shared secret the extension sends as X-Guardian-Token)."
            )
        oauth = _clean(e.get("CLAUDE_CODE_OAUTH_TOKEN"))
        if not oauth:
            raise ConfigError(
                "CLAUDE_CODE_OAUTH_TOKEN is not set. Run `claude setup-token` and add it to .env."
            )
        if _clean(e.get("ANTHROPIC_API_KEY")):
            raise ConfigError(
                "ANTHROPIC_API_KEY is set; it overrides the Max subscription. Unset it."
            )
        config_dir = _clean(e.get("CLAUDE_CONFIG_DIR"))
        if not config_dir:
            raise ConfigError("CLAUDE_CONFIG_DIR is not set (use scripts/launch-guardian.sh).")
        return cls(
            host=_clean(e.get("GUARDIAN_HOST")) or DEFAULT_HOST,
            port=int(_clean(e.get("GUARDIAN_PORT")) or DEFAULT_PORT),
            token=token,
            cache_path=_clean(e.get("GUARDIAN_CACHE_PATH")) or DEFAULT_CACHE_PATH,
            event_log_path=_clean(e.get("GUARDIAN_EVENT_LOG_PATH")) or DEFAULT_EVENT_LOG_PATH,
            classify_timeout_s=float(
                _clean(e.get("GUARDIAN_CLASSIFY_TIMEOUT")) or DEFAULT_TIMEOUT_S
            ),
            screenshot_confidence_threshold=float(
                _clean(e.get("GUARDIAN_SCREENSHOT_THRESHOLD")) or DEFAULT_SCREENSHOT_THRESHOLD
            ),
            enable_vision=_clean(e.get("GUARDIAN_ENABLE_VISION")).lower() in ("1", "true", "yes"),
            model=_clean(e.get("GUARDIAN_MODEL")) or DEFAULT_MODEL,
            config_dir=config_dir,
            oauth_token=oauth,
        )
