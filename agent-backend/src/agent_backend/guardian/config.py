"""Validated configuration for the guardian service."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from ..config import (
    DEFAULT_AI_PROVIDER,
    DEFAULT_CODEX_AGENT_MODEL,
    DEFAULT_CODEX_MODEL,
    ConfigError,
    require_claude_subscription,
    require_codex_home,
    resolve_ai_provider,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 2947
DEFAULT_METRICS_PORT = 2948
DEFAULT_MODEL = "claude-haiku-4-5"  # fast + cheap for per-page classification
DEFAULT_AGENT_MODEL = "claude-sonnet-4-5"  # guardian Agent chat: deeper reasoning than classify
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
# Cap on bonus minutes a teen can self-redeem from prize points per household-local day
# (independent of parent grants). 0 disables self-serve redemption.
DEFAULT_PRIZE_DAILY_BONUS_CAP_MIN = 120
# Directory holding the packed extension CRX + update manifest (written by
# scripts/pack-extension.sh) that the kid browser force-installs via policy.
DEFAULT_EXT_DIST_DIR = ".chromium-dist"
# What /classify and /search-classify answer when the classifier errors or times out:
# "open" allows the page (no false blocks during guardian hiccups), "closed" blocks it
# (strictest: nothing unclassified gets through). Per-household choice.
CLASSIFY_FAIL_MODES = frozenset({"open", "closed"})
DEFAULT_CLASSIFY_FAIL_MODE = "open"


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
    # Household timezone (IANA name, e.g. "America/Los_Angeles") used for screen-time day
    # boundaries and bedtime windows. Empty → the server's local timezone.
    household_tz: str = ""
    # Max bonus minutes a teen may self-redeem from prize points per day (0 disables it).
    prize_daily_bonus_cap_min: int = DEFAULT_PRIZE_DAILY_BONUS_CAP_MIN
    # Directory with the packed extension CRX + update manifest served at /ext/* so the
    # kid browser can force-install the extension. Relative paths resolve from the
    # service working directory (agent-backend/), matching the data/ convention.
    ext_dist_dir: str = DEFAULT_EXT_DIST_DIR
    # Model backing the conversational guardian "Agent" assistant (data analysis, suggestions,
    # config proposals). Defaults to a stronger model than the per-page classifier (``model``),
    # which stays on the fast/cheap tier. Override with GUARDIAN_AGENT_MODEL.
    agent_model: str = DEFAULT_AGENT_MODEL
    # Verdict when the classifier errors/times out: "open" allows, "closed" blocks.
    classify_fail_mode: str = DEFAULT_CLASSIFY_FAIL_MODE
    # Headless AI provider: "claude" (Claude Max subscription) or "codex" (ChatGPT
    # subscription via the codex CLI). Selected by AEGIS_AI_PROVIDER; chooses the backend
    # the Classifier builds and which auth fields below are populated.
    ai_provider: str = DEFAULT_AI_PROVIDER
    # Codex config+auth directory ($CODEX_HOME/auth.json). Set only when ai_provider="codex".
    codex_home: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> GuardianConfig:
        e = os.environ if env is None else env
        # GUARDIAN_TOKEN is optional: a profiles file (GUARDIAN_PROFILES_PATH) can supply
        # per-teen tokens instead. load_profiles enforces that at least one identity exists.
        token = _clean(e.get("GUARDIAN_TOKEN"))
        # Provider-scoped auth: claude needs the OAuth token + isolated config dir; codex
        # needs a signed-in CODEX_HOME. Model defaults track the chosen provider.
        provider = resolve_ai_provider(e)
        if provider == "claude":
            oauth = require_claude_subscription(e)
            config_dir = _clean(e.get("CLAUDE_CONFIG_DIR"))
            if not config_dir:
                raise ConfigError("CLAUDE_CONFIG_DIR is not set (use scripts/launch-guardian.sh).")
            codex_home = ""
            default_model, default_agent_model = DEFAULT_MODEL, DEFAULT_AGENT_MODEL
        else:
            oauth, config_dir = "", ""
            codex_home = require_codex_home(e)
            default_model, default_agent_model = DEFAULT_CODEX_MODEL, DEFAULT_CODEX_AGENT_MODEL
        metrics_port = int(_clean(e.get("GUARDIAN_METRICS_PORT")) or DEFAULT_METRICS_PORT)
        if not 1024 <= metrics_port <= 65535:
            raise ConfigError("GUARDIAN_METRICS_PORT must be between 1024 and 65535.")
        fail_mode = (
            _clean(e.get("GUARDIAN_CLASSIFY_FAIL_MODE")).lower() or DEFAULT_CLASSIFY_FAIL_MODE
        )
        if fail_mode not in CLASSIFY_FAIL_MODES:
            raise ConfigError(
                f"GUARDIAN_CLASSIFY_FAIL_MODE must be 'open' or 'closed', got {fail_mode!r}."
            )
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
            household_tz=_clean(e.get("GUARDIAN_HOUSEHOLD_TZ")),
            prize_daily_bonus_cap_min=max(
                0,
                int(
                    _clean(e.get("GUARDIAN_PRIZE_DAILY_CAP_MIN"))
                    or DEFAULT_PRIZE_DAILY_BONUS_CAP_MIN
                ),
            ),
            ext_dist_dir=_clean(e.get("GUARDIAN_EXT_DIST_DIR")) or DEFAULT_EXT_DIST_DIR,
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
            model=_clean(e.get("GUARDIAN_MODEL")) or default_model,
            agent_model=_clean(e.get("GUARDIAN_AGENT_MODEL")) or default_agent_model,
            config_dir=config_dir,
            oauth_token=oauth,
            classify_fail_mode=fail_mode,
            ai_provider=provider,
            codex_home=codex_home,
        )
