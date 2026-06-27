"""Validated configuration loaded from the environment.

Two small frozen configs:
- ``BrowserConfig`` — what the MCP/browser layer needs (CDP endpoint).
- ``RunnerConfig`` — what the AI runner needs (subscription auth + model).

The backend can run on one of two **headless AI providers**, selected by
``AEGIS_AI_PROVIDER`` (``claude`` | ``codex``, default ``claude``):
- ``claude`` — the ``claude`` CLI via a Claude Max subscription OAuth token.
- ``codex``  — the ``codex`` CLI via a ChatGPT subscription (auth stored under ``CODEX_HOME``).

The provider-resolution and provider-scoped auth helpers below are shared by both
``RunnerConfig`` and ``guardian.config.GuardianConfig`` so the two stay in lock-step.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_MODEL = "claude-sonnet-4-5"

# --- AI provider selection -----------------------------------------------------
AI_PROVIDERS = frozenset({"claude", "codex"})
DEFAULT_AI_PROVIDER = "claude"
# Codex models (ChatGPT subscription via the `codex` CLI). Overridable per env var;
# set these to whatever model IDs the parent's ChatGPT plan exposes (`codex` lists them).
DEFAULT_CODEX_MODEL = "gpt-5-codex"  # fast tier for per-page classification
DEFAULT_CODEX_AGENT_MODEL = "gpt-5-codex"  # stronger tier for agent chat / browser runner


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


def _clean(value: str | None) -> str:
    return (value or "").strip()


def resolve_ai_provider(env: Mapping[str, str]) -> str:
    """Return the validated ``AEGIS_AI_PROVIDER`` (defaults to ``claude``)."""
    provider = _clean(env.get("AEGIS_AI_PROVIDER")).lower() or DEFAULT_AI_PROVIDER
    if provider not in AI_PROVIDERS:
        raise ConfigError(
            f"AEGIS_AI_PROVIDER must be one of {sorted(AI_PROVIDERS)}, got {provider!r}."
        )
    return provider


def require_claude_subscription(env: Mapping[str, str]) -> str:
    """Validate Claude Max subscription auth; return the OAuth token.

    The OAuth token (not an API key) is how the backend bills against the flat-rate Max
    subscription. ``ANTHROPIC_API_KEY`` is rejected because it would override the token and
    silently switch to per-token API billing.
    """
    token = _clean(env.get("CLAUDE_CODE_OAUTH_TOKEN"))
    if not token:
        raise ConfigError(
            "CLAUDE_CODE_OAUTH_TOKEN is not set. Run `claude setup-token` and add it to "
            "agent-backend/.env to authenticate with your Claude Max subscription."
        )
    if _clean(env.get("ANTHROPIC_API_KEY")):
        raise ConfigError(
            "ANTHROPIC_API_KEY is set; it takes precedence over the Max subscription and "
            "would bill per-token. Unset it so the OAuth token is used."
        )
    return token


def require_codex_home(env: Mapping[str, str]) -> str:
    """Validate Codex (ChatGPT subscription) auth; return the resolved ``CODEX_HOME``.

    Codex stores its OAuth session in ``$CODEX_HOME/auth.json`` and refreshes it in place,
    so the only thing the backend must verify is that a sign-in has happened.
    """
    codex_home = _clean(env.get("CODEX_HOME"))
    if not codex_home:
        raise ConfigError(
            "CODEX_HOME is not set. Run scripts/configure-ai-provider.sh (or set CODEX_HOME "
            "in agent-backend/.env) and sign in with `codex login`."
        )
    if not (Path(codex_home) / "auth.json").exists():
        raise ConfigError(
            f"CODEX_HOME={codex_home!r}: auth.json not found — Codex is not signed in. "
            f"Run:  CODEX_HOME={codex_home!r} codex login"
        )
    return codex_home


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
    """Configuration for the autonomous browser runner (Claude or Codex subscription).

    ``ai_provider`` selects the backend; the auth fields are provider-scoped — ``oauth_token``
    is set for ``claude``, ``codex_home`` for ``codex`` (the other is empty).
    """

    oauth_token: str
    model: str
    cdp_url: str
    ai_provider: str = DEFAULT_AI_PROVIDER
    codex_home: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RunnerConfig:
        resolved = os.environ if env is None else env
        provider = resolve_ai_provider(resolved)
        if provider == "claude":
            oauth_token = require_claude_subscription(resolved)
            codex_home = ""
            default_model = DEFAULT_MODEL
        else:
            oauth_token = ""
            codex_home = require_codex_home(resolved)
            default_model = DEFAULT_CODEX_AGENT_MODEL
        return cls(
            oauth_token=oauth_token,
            model=_clean(resolved.get("AGENT_MODEL")) or default_model,
            cdp_url=_resolve_cdp_url(resolved),
            ai_provider=provider,
            codex_home=codex_home,
        )
