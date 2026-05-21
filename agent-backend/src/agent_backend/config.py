"""Validated configuration loaded from the environment.

Two small frozen configs:
- ``BrowserConfig`` — what the MCP/browser layer needs (CDP endpoint).
- ``RunnerConfig`` — what the Claude Agent SDK runner needs (subscription auth + model).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_MODEL = "claude-sonnet-4-5"


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _resolve_cdp_url(env: Mapping[str, str]) -> str:
    cdp_url = _clean(env.get("CHROMIUM_CDP_URL")) or DEFAULT_CDP_URL
    if not cdp_url.startswith(("http://", "https://")):
        raise ConfigError(f"CHROMIUM_CDP_URL must be an http(s) URL, got: {cdp_url!r}")
    return cdp_url


@dataclass(frozen=True, slots=True)
class BrowserConfig:
    """Configuration for the browser-control layer."""

    cdp_url: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> BrowserConfig:
        resolved = os.environ if env is None else env
        return cls(cdp_url=_resolve_cdp_url(resolved))


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    """Configuration for the Claude Agent SDK runner (Claude Max subscription)."""

    oauth_token: str
    model: str
    cdp_url: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RunnerConfig:
        resolved = os.environ if env is None else env
        token = _clean(resolved.get("CLAUDE_CODE_OAUTH_TOKEN"))
        if not token:
            raise ConfigError(
                "CLAUDE_CODE_OAUTH_TOKEN is not set. Run `claude setup-token` and add it to "
                "agent-backend/.env to authenticate with your Claude Max subscription."
            )
        if _clean(resolved.get("ANTHROPIC_API_KEY")):
            raise ConfigError(
                "ANTHROPIC_API_KEY is set; it takes precedence over the Max subscription and "
                "would bill per-token. Unset it so the OAuth token is used."
            )
        return cls(
            oauth_token=token,
            model=_clean(resolved.get("AGENT_MODEL")) or DEFAULT_MODEL,
            cdp_url=_resolve_cdp_url(resolved),
        )
