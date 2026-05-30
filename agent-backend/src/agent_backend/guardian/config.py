"""Validated configuration for the guardian service."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from ..config import ConfigError

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 2947
DEFAULT_METRICS_PORT = 2948
DEFAULT_MODEL = "claude-haiku-4-5"  # fast + cheap for per-page classification
DEFAULT_TIMEOUT_S = 180.0  # a classification spawns a `claude` subprocess; give it room
DEFAULT_SCREENSHOT_THRESHOLD = 0.6
DEFAULT_AGE = 10  # default child age for age-aware classification (per-profile override)
MIN_AGE = 1  # accepted per-profile age bounds (set_age rejects outside; the loader clamps)
MAX_AGE = 25
DEFAULT_CACHE_PATH = "data/guardian_cache.db"
DEFAULT_EVENT_LOG_PATH = "data/guardian_events.jsonl"
DEFAULT_SUMMARY_LOG_PATH = "data/guardian_summaries.jsonl"
DEFAULT_WHITELIST_PATH = "data/guardian_whitelist.json"
DEFAULT_BLOCKLIST_PATH = "data/guardian_blocklist.json"
DEFAULT_REQUESTS_PATH = "data/guardian_requests.json"
DEFAULT_PROMPT_PATH = "data/guardian_prompt.txt"
DEFAULT_SEARCH_ALLOW_PATH = "data/guardian_search_allow.json"
DEFAULT_SEARCH_BLOCK_PATH = "data/guardian_search_block.json"
DEFAULT_PROFILES_PATH = "data/guardian_profiles.json"
DEFAULT_ADMIN_PATH = "data/guardian_admin.json"


def _clean(value: str | None) -> str:
    return (value or "").strip()


@dataclass(frozen=True, slots=True)
class GuardianConfig:
    host: str
    port: int
    metrics_port: int
    token: str
    cache_path: str
    event_log_path: str
    whitelist_path: str
    blocklist_path: str
    requests_path: str
    parent_pin: str
    classify_timeout_s: float
    screenshot_confidence_threshold: float
    enable_vision: bool
    model: str
    config_dir: str
    oauth_token: str
    # Path to the teen-profiles registry (JSON). Empty/absent → single "default" profile.
    profiles_path: str = ""
    # Path to the parent-PIN hash file written by the first-run /setup wizard (PinStore).
    admin_path: str = DEFAULT_ADMIN_PATH
    # Legacy single-profile classification prompt; per-profile prompts live under data/profiles/.
    prompt_path: str = DEFAULT_PROMPT_PATH
    # Legacy single-profile search-keyword lists; per-profile lists live under data/profiles/.
    search_allow_path: str = DEFAULT_SEARCH_ALLOW_PATH
    search_block_path: str = DEFAULT_SEARCH_BLOCK_PATH
    # Path to saved AI activity-summary runs (JSONL): dashboard panel + Activity "Summaries" tab.
    summary_log_path: str = DEFAULT_SUMMARY_LOG_PATH

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> GuardianConfig:
        e = os.environ if env is None else env
        # GUARDIAN_TOKEN is optional: a profiles file (GUARDIAN_PROFILES_PATH) can supply
        # per-teen tokens instead. load_profiles enforces that at least one identity exists.
        token = _clean(e.get("GUARDIAN_TOKEN"))
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
        metrics_port = int(_clean(e.get("GUARDIAN_METRICS_PORT")) or DEFAULT_METRICS_PORT)
        if not 1024 <= metrics_port <= 65535:
            raise ConfigError("GUARDIAN_METRICS_PORT must be between 1024 and 65535.")
        return cls(
            host=_clean(e.get("GUARDIAN_HOST")) or DEFAULT_HOST,
            port=int(_clean(e.get("GUARDIAN_PORT")) or DEFAULT_PORT),
            metrics_port=metrics_port,
            token=token,
            cache_path=_clean(e.get("GUARDIAN_CACHE_PATH")) or DEFAULT_CACHE_PATH,
            event_log_path=_clean(e.get("GUARDIAN_EVENT_LOG_PATH")) or DEFAULT_EVENT_LOG_PATH,
            summary_log_path=(
                _clean(e.get("GUARDIAN_SUMMARY_LOG_PATH")) or DEFAULT_SUMMARY_LOG_PATH
            ),
            whitelist_path=_clean(e.get("GUARDIAN_WHITELIST_PATH")) or DEFAULT_WHITELIST_PATH,
            blocklist_path=_clean(e.get("GUARDIAN_BLOCKLIST_PATH")) or DEFAULT_BLOCKLIST_PATH,
            requests_path=_clean(e.get("GUARDIAN_REQUESTS_PATH")) or DEFAULT_REQUESTS_PATH,
            prompt_path=_clean(e.get("GUARDIAN_PROMPT_PATH")) or DEFAULT_PROMPT_PATH,
            search_allow_path=(
                _clean(e.get("GUARDIAN_SEARCH_ALLOW_PATH")) or DEFAULT_SEARCH_ALLOW_PATH
            ),
            search_block_path=(
                _clean(e.get("GUARDIAN_SEARCH_BLOCK_PATH")) or DEFAULT_SEARCH_BLOCK_PATH
            ),
            profiles_path=_clean(e.get("GUARDIAN_PROFILES_PATH")) or DEFAULT_PROFILES_PATH,
            admin_path=_clean(e.get("GUARDIAN_ADMIN_PATH")) or DEFAULT_ADMIN_PATH,
            parent_pin=_clean(e.get("GUARDIAN_PARENT_PIN")),
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
