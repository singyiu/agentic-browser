"""Starlette HTTP service exposing POST /classify and GET /health."""

from __future__ import annotations

import asyncio
import functools
import hmac
import json
import re
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .. import __version__ as _BACKEND_VERSION
from ..config import ConfigError
from .access_requests import AccessRequest, RequestStore
from .blocklist import BlocklistStore
from .cache import VerdictCache
from .classifier import Classifier
from .config import DEFAULT_AGE, MAX_AGE, MIN_AGE, GuardianConfig
from .event_log import EventLog
from .keyword_store import KeywordStore
from .metrics import GuardianMetrics
from .normalize import extract_host, normalize_url
from .pin_store import PinStore, validate_pin_format
from .prize_points import (
    POINTS_PER_MINUTE,
    REDEEM_PACKAGES_MIN,
    PrizePointStore,
    cost_for_minutes,
    redeemed_minutes_today,
)
from .profile_manager import (
    InvalidProfileNameError,
    ProfileExistsError,
    ProfileManager,
    ProfileNotFoundError,
)
from .profiles import DEFAULT_PROFILE_NAME, GLOBAL_PROFILE_NAME, ProfileRegistry
from .prompt import PromptStore, default_profile_prompt
from .runtime import ProfileRuntime, build_runtime
from .search_classifier import classify_search_query
from .time_ledger import TimeLedger
from .time_policy import (
    TimePolicy,
    TimePolicyStore,
)
from .time_policy import (
    from_stored as time_policy_from_stored,
)
from .time_policy import (
    parse_policy as parse_time_policy,
)
from .time_policy import (
    resolve as resolve_time_policy,
)
from .time_policy import (
    to_json as time_policy_to_json,
)
from .time_requests import TimeRequestStore
from .verdict import Verdict
from .whitelist import WhitelistStore, canonicalize_url, classify_entry

# Per-URL verdict events shown in the parent Activity view; admin/dwell events are excluded so
# the timeline reads as "what each kid saw and how it was decided".
ACTIVITY_EVENTS: tuple[str, ...] = (
    "allow",
    "block",
    "blocklist_block",
    "whitelist_allow",
    "cache_hit",
    "fail_open",
    "escalate",
)
ACTIVITY_LIMIT_DEFAULT = 100
ACTIVITY_LIMIT_MAX = 500

# Prize-point change events: the feed behind the Activity "Prize points" tab. Kept out of
# ACTIVITY_EVENTS so the per-URL timeline stays "what each kid saw", not the points ledger.
PRIZE_EVENTS: tuple[str, ...] = ("prize_points_earned", "prize_points_redeemed")

# AI activity-summary tuning. A saved summary older than this is "stale" → the dashboard
# auto-regenerates it on load; summaries review a wider window than the timeline default.
SUMMARY_STALE_AFTER_S = 48 * 3600
SUMMARY_LIMIT_DEFAULT = 200

# Screen-time request limits + the prompt that turns a parent's natural-language limits
# into the structured TimePolicy JSON (validated/clamped by time_policy.parse_policy).
_MAX_TIME_TEXT = 2000
_MAX_TIME_REASON = 500
_MAX_REQUEST_MINUTES = 1440
_TIME_POLICY_SYSTEM_PROMPT = (
    "You convert a parent's natural-language screen-time rules into a strict JSON object for "
    "a parental-control browser. Output ONLY the JSON object, no prose.\n\n"
    "Schema:\n"
    "{\n"
    '  "daily_minutes": {"default": <int minutes>, "mon": <int>, ..., "sun": <int>},\n'
    '  "windows": [{"days": ["mon", ...], "start": "HH:MM", "end": "HH:MM"}],\n'
    '  "sites": [{"host": "example.com", "daily_minutes": <int or null>, "excluded": <bool>}]\n'
    "}\n\n"
    "Rules:\n"
    '- daily_minutes: the general daily browsing budget in MINUTES. Use "default" for every '
    "day; add per-day keys (mon..sun) only to override specific days. Omit days not mentioned.\n"
    '- windows: bedtime / blocked hours in 24h "HH:MM". An "end" earlier than "start" '
    'wraps past midnight (e.g. 21:00 -> 07:00). Empty "days" means every day.\n'
    '- sites: per-site overrides. "excluded": true means time on that site does NOT count '
    "toward the general budget and the site stays usable after the budget runs out (use for "
    'educational/homework sites). "daily_minutes" is an optional separate cap (null = none).\n'
    "- Use only the fields the parent mentioned; omit the rest. Minutes are 0..1440.\n"
    "Reply with ONLY the JSON object."
)


def _clamp_request_minutes(value: object) -> int | None:
    """A teen's requested-minutes ask: int in [1, 1440], else None (let the parent decide)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    n = int(value)
    return n if 1 <= n <= _MAX_REQUEST_MINUTES else None


# A parent's prize-point grant is a signed delta (positive award or negative correction);
# bound the magnitude so a fat-fingered value can't mint an absurd balance.
_MAX_PRIZE_POINTS = 100_000


def _clamp_prize_points(value: object) -> int | None:
    """Parse a grant delta: a non-zero int within ±_MAX_PRIZE_POINTS, else None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value != 0 and abs(value) <= _MAX_PRIZE_POINTS else None


def _block_reason(usage: object) -> str:
    """A short machine hint for why the current host is blocked (or "" when it is not)."""
    u = usage
    if not u.blocked:  # type: ignore[attr-defined]
        return ""
    site = u.site  # type: ignore[attr-defined]
    if site is not None and site.excluded:
        return "site_limit" if site.blocked else ""
    if u.bedtime_active:  # type: ignore[attr-defined]
        return "bedtime"
    if u.blocked_general:  # type: ignore[attr-defined]
        return "time_limit"
    if site is not None and site.blocked:
        return "site_limit"
    return ""


def _usage_to_json(usage: object) -> dict[str, Any]:
    """Serialize a :class:`time_ledger.Usage` to the time-state response envelope."""
    u = usage
    site = u.site  # type: ignore[attr-defined]
    return {
        "general": {
            "used_ms": u.general_used_ms,  # type: ignore[attr-defined]
            "limit_ms": u.general_limit_ms,  # type: ignore[attr-defined]
            "remaining_ms": u.general_remaining_ms,  # type: ignore[attr-defined]
            "blocked": u.blocked_general,  # type: ignore[attr-defined]
        },
        "bedtime": {"active": u.bedtime_active},  # type: ignore[attr-defined]
        "site": (
            None
            if site is None
            else {
                "host": site.host,
                "excluded": site.excluded,
                "used_ms": site.used_ms,
                "limit_ms": site.limit_ms,
                "remaining_ms": site.remaining_ms,
                "blocked": site.blocked,
            }
        ),
        "blocked": u.blocked,  # type: ignore[attr-defined]
        "reason": _block_reason(u),
    }


def _parse_activity_limit(raw: str | None) -> int:
    """Clamp ?limit= to [1, ACTIVITY_LIMIT_MAX]; fall back to the default when absent/invalid."""
    try:
        value = ACTIVITY_LIMIT_DEFAULT if raw is None else int(raw)
    except ValueError:
        return ACTIVITY_LIMIT_DEFAULT
    return max(1, min(value, ACTIVITY_LIMIT_MAX))


_MAX_RULE_SUGGESTIONS = 8
_RULE_KINDS = ("exact", "wildcard", "nl", "content", "ai")
_SUGGESTION_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def _parse_rule_suggestions(raw: str, *, max_items: int = _MAX_RULE_SUGGESTIONS) -> list[dict]:
    """Best-effort extraction of a ``[{kind, value, reason}, ...]`` array from model output.

    Mirrors ``parse_verdict``'s fail-safe stance: any malformed / non-JSON / oversized output
    yields ``[]`` (never raises), so a confused model can never 500 the endpoint. Each item is
    validated like a list entry (``value``: non-empty, <=512 chars, single-line printable);
    ``kind`` is clamped to a known label (default ``"content"``); ``reason`` is trimmed.
    """
    if not raw:
        return []
    text = raw.strip()
    candidates = [text]
    match = _SUGGESTION_ARRAY_RE.search(text)
    if match is not None:
        candidates.append(match.group(0))
    data: object = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, list):
            break
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value", "")).strip()
        if not value or len(value) > 512 or not value.isprintable():
            continue
        kind = str(item.get("kind", "")).strip().lower()
        if kind not in _RULE_KINDS:
            kind = "content"
        reason = str(item.get("reason", "")).strip()[:300]
        out.append({"kind": kind, "value": value, "reason": reason})
        if len(out) >= max_items:
            break
    return out


def _summarize_activity(events: list[dict], *, max_lines: int = 60) -> str:
    """One bounded ``- host (outcome, who)`` line per recent event, for the suggest-rules prompt."""
    lines: list[str] = []
    for ev in events[:max_lines]:
        url = str(ev.get("url") or ev.get("url_key") or "").strip()
        if not url:
            continue
        host = extract_host(url) or url
        blocked = str(ev.get("event", "")) in ("block", "blocklist_block")
        who = str(ev.get("profile") or "").strip()
        suffix = f", {who}" if who else ""
        lines.append(f"- {host} ({'blocked' if blocked else 'allowed'}{suffix})")
    return "\n".join(lines) if lines else "(none)"


_MAX_SUMMARY_PROFILES = 12
_MAX_SUMMARY_ITEMS = 6
_SUMMARY_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _clean_str_list(
    value: object, *, max_len: int, max_items: int = _MAX_SUMMARY_ITEMS
) -> list[str]:
    """Coerce model-supplied ``trends``/``attention`` into a bounded list of clean strings.

    Non-list input, non-string entries, and blanks are dropped; each kept string is trimmed and
    truncated, and the list is capped — so a confused model can't bloat the saved summary.
    """
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            continue
        text = entry.strip()[:max_len]
        if text:
            out.append(text)
        if len(out) >= max_items:
            break
    return out


def _parse_activity_summary(raw: str) -> dict:
    """Best-effort extraction of ``{"profiles":[{profile,summary,trends[],attention[]}]}``.

    Mirrors ``_parse_rule_suggestions``' fail-safe stance: any malformed / non-JSON output yields
    ``{"profiles": []}`` (never raises), so a confused model can't 500 the endpoint. Each profile
    needs a non-empty name; ``summary`` is trimmed/clamped; ``trends``/``attention`` are cleaned
    lists; profiles are capped.
    """
    empty: dict = {"profiles": []}
    if not raw:
        return empty
    text = raw.strip()
    candidates = [text]
    match = _SUMMARY_OBJECT_RE.search(text)
    if match is not None:
        candidates.append(match.group(0))
    data: object = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            break
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), list):
        return empty
    out: list[dict] = []
    for item in data["profiles"]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("profile", "")).strip()[:80]
        if not name:
            continue
        out.append(
            {
                "profile": name,
                "summary": str(item.get("summary", "")).strip()[:600],
                "trends": _clean_str_list(item.get("trends"), max_len=200),
                "attention": _clean_str_list(item.get("attention"), max_len=240),
            }
        )
        if len(out) >= _MAX_SUMMARY_PROFILES:
            break
    return {"profiles": out}


def _summary_is_stale(ts: str, *, now: datetime | None = None) -> bool:
    """Whether a saved summary's timestamp is old enough to auto-regenerate.

    Blank or unparseable timestamps are treated as stale (safe default: prefer regenerating).
    """
    if not ts:
        return True
    try:
        generated = datetime.fromisoformat(ts)
    except ValueError:
        return True
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    return (current - generated).total_seconds() > SUMMARY_STALE_AFTER_S


def _activity_digest(events: list[dict], ages: dict[str, int], *, max_lines: int = 80) -> str:
    """Per-profile compact digest of recent activity for the summary prompt.

    Only known teen/kid profiles are included — the Global profile and any untagged events are
    skipped, so the summary is always per real child. Groups by profile with timestamp, host,
    outcome, and any matched categories, so the model can spot blocked-site attempts, risky
    content, new/unusual sites, and odd-hour browsing.
    """
    by_profile: dict[str, list[str]] = {}
    total = 0
    for ev in events:
        if total >= max_lines:
            break
        who = str(ev.get("profile") or "").strip()
        if who not in ages:  # excludes the Global profile and untagged/unknown events
            continue
        url = str(ev.get("url") or ev.get("url_key") or "").strip()
        if not url:
            continue
        host = extract_host(url) or url
        blocked = str(ev.get("event", "")) in ("block", "blocklist_block")
        stamp = str(ev.get("ts") or "")[:16]  # YYYY-MM-DDTHH:MM — enough for time-of-day
        cats = ev.get("categories_matched")
        cat_txt = ""
        if isinstance(cats, list) and cats:
            cat_txt = " [" + ", ".join(str(c) for c in cats[:3]) + "]"
        outcome = "blocked" if blocked else "allowed"
        by_profile.setdefault(who, []).append(f"- {stamp} {host} ({outcome}){cat_txt}")
        total += 1
    if not by_profile:
        return "(no recent activity)"
    blocks = [
        f"PROFILE: {who} (age {ages.get(who, DEFAULT_AGE)})\n" + "\n".join(rows)
        for who, rows in by_profile.items()
    ]
    return "\n\n".join(blocks)


_MAX_PROMPT_CHARS = 4000
_MAX_QUERY_CHARS = 500  # max length of a search query checked by /search-classify


def _valid_prompt_text(text: str) -> bool:
    """A bounded, multi-line classification prompt: printable text plus tabs/newlines.

    Unlike a single list entry (which uses ``str.isprintable``), a prompt may span lines, so
    newlines and tabs are allowed and other control characters are rejected. Empty is valid
    (it resets the profile to its age-band default).
    """
    if len(text) > _MAX_PROMPT_CHARS:
        return False
    return all(ch in "\n\t" or ch.isprintable() for ch in text)


# --- Stack version assembly (the Agent page + GET /version) -----------------------------------
# Read the deployed component versions at request time from their source-of-truth files rather
# than hardcoding them. The repo root is resolved from this file's location; any unreadable
# source degrades to ``None`` (never a 500). The guardian runs from a checkout, so these paths
# exist; in an installed-elsewhere layout the corresponding fields are simply ``None``.
_GUARDIAN_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _GUARDIAN_DIR.parents[3]  # guardian → agent_backend → src → agent-backend → <repo>
_LGTM_IMAGE_PREFIX = "grafana/otel-lgtm:"
_ALLOY_IMAGE_PREFIX = "grafana/alloy:"


def _read_extension_version() -> str | None:
    """The kid extension's ``version`` from ``extension/manifest.json`` (None if unreadable)."""
    try:
        data = json.loads((_REPO_ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = data.get("version")
    return version if isinstance(version, str) else None


def _read_grafana_versions() -> dict[str, str | None]:
    """The pinned Grafana LGTM + Alloy image tags from ``observability/docker-compose.yml``."""
    lgtm: str | None = None
    alloy: str | None = None
    try:
        text = (_REPO_ROOT / "observability" / "docker-compose.yml").read_text(encoding="utf-8")
    except OSError:
        return {"lgtm": None, "alloy": None}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if _LGTM_IMAGE_PREFIX in line:
            lgtm = line.split(_LGTM_IMAGE_PREFIX, 1)[1].strip().strip("\"'") or None
        elif _ALLOY_IMAGE_PREFIX in line:
            alloy = line.split(_ALLOY_IMAGE_PREFIX, 1)[1].strip().strip("\"'") or None
    return {"lgtm": lgtm, "alloy": alloy}


def _stack_versions(agent_model: str) -> dict[str, Any]:
    """Assemble the ``{guardian, extension, grafana, model}`` stack-version payload."""
    return {
        "guardian": _BACKEND_VERSION,
        "extension": _read_extension_version(),
        "grafana": _read_grafana_versions(),
        "model": agent_model,
    }


# --- Agent chat: context assembly + structured-envelope parsing (POST /agent/chat) ------------
# The flagship guardian "Agent" page. Each turn assembles bounded server-side context (software
# digest, per-profile config snapshot, a recent-activity slice, stack versions) and makes ONE
# stateless ``Classifier.generate()`` call. The model replies with a JSON envelope: a markdown
# reply plus optional config-change proposals (the parent applies them via /agent/apply — the
# model never writes) and follow-up suggestions. Parsing is fail-safe (mirrors
# ``_parse_activity_summary``): any malformed output degrades to a plain reply, never a 500.

# The agent's entire write surface — the action keys it may PROPOSE and the parent may APPLY.
# Destructive operations (PIN change, profile delete/rename, token regeneration) are deliberately
# absent: an LLM must never be able to even suggest them through this channel.
_AGENT_APPLY_ACTIONS: frozenset[str] = frozenset(
    {
        "whitelist.add",
        "whitelist.remove",
        "blocklist.add",
        "blocklist.remove",
        "search_allow.add",
        "search_allow.remove",
        "search_block.add",
        "search_block.remove",
        "time_policy.set",
        "prize.grant",
        "prompt.set",
    }
)

_AGENT_HISTORY_MAX = 20  # conversation turns kept (server-enforced cap on token cost)
_AGENT_MSG_MAX_CHARS = 4000  # per-message clamp
_AGENT_REPLY_MAX = 8000  # reply clamp
_AGENT_MAX_PROPOSALS = 5
_AGENT_MAX_SUGGESTIONS = 6
_AGENT_RATIONALE_MAX = 300
_AGENT_SUGGESTION_MAX = 80
_AGENT_LIST_CAP = 50  # config-list entries shown per profile
_AGENT_ACTIVITY_LIMIT = 150  # events pulled for the data slice
_AGENT_ACTIVITY_MAX_LINES = 80  # rendered activity lines


class _AgentApplyError(Exception):
    """A controlled failure while applying an agent-proposed change → an HTTP status + message."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


_AGENT_SYSTEM_HEADER = (
    "You are the Aegis Guardian assistant — the AI agent inside a parental-control browser "
    "system. A parent ('guardian') is talking to you in their dashboard. Their child uses a "
    "locked Chromium browser whose extension classifies and blocks pages through this guardian "
    "backend. Per-profile rules control allowed/blocked sites, allowed/blocked search keywords, "
    "daily screen-time budgets and bedtime, prize points (kids redeem them for bonus screen "
    "time), and a custom classification prompt.\n\n"
    "You can: answer questions about how the system works; analyze the configuration and recent "
    "activity shown below; and PROPOSE configuration changes for the parent to approve. You never "
    "change anything yourself — you propose, and the parent applies.\n\n"
    "Respond with ONLY a single JSON object (no prose outside it, no code fences):\n"
    '{"reply": "<markdown answer for the parent>", '
    '"proposals": [{"action": "<key>", "profile": "<profile name or null>", '
    '"params": {...}, "rationale": "<one sentence>"}], '
    '"suggestions": ["<short follow-up the parent might tap>"]}\n\n'
    "Propose an action ONLY when the parent clearly wants a change. Allowed actions and params:\n"
    "- whitelist.add / whitelist.remove {entry}: allow / un-allow a site or topic\n"
    "- blocklist.add / blocklist.remove {entry}: block / unblock a site or topic\n"
    "- search_allow.add / search_allow.remove {entry}: allow / un-allow a search keyword\n"
    "- search_block.add / search_block.remove {entry}: block / unblock a search keyword\n"
    "- time_policy.set {text}: set screen-time rules from a natural-language description\n"
    "- prize.grant {points, reason}: award (negative deducts) prize points to a child\n"
    "- prompt.set {prompt}: replace a profile's custom classification prompt\n"
    "'profile' is one of the profile names listed below, or 'global' for the shared rules that "
    "apply to every child (prize.grant must name a specific child, never global). Keep 'reply' "
    "concise and warm. Use empty lists when there is nothing to propose or suggest."
)


def _coerce_chat_messages(value: object) -> list[dict[str, str]]:
    """Validate conversation history → clean ``{role, content}`` list, bounded to the last N.

    Non-list input, non-dict entries, unknown roles, and non-string/blank content are dropped;
    each message is clamped and only the most recent ``_AGENT_HISTORY_MAX`` are kept (a
    server-enforced cap so a long client history can't blow up the prompt).
    """
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip()
        if role not in ("user", "assistant"):
            continue
        content = item.get("content")
        if not isinstance(content, str):
            continue
        text = content.strip()[:_AGENT_MSG_MAX_CHARS]
        if text:
            out.append({"role": role, "content": text})
    return out[-_AGENT_HISTORY_MAX:]


def _render_conversation(messages: list[dict[str, str]]) -> str:
    """Render the bounded history as a labeled transcript for the user prompt."""
    labels = {"user": "Guardian", "assistant": "Assistant"}
    return "\n\n".join(f"{labels[m['role']]}: {m['content']}" for m in messages)


def _parse_agent_proposals(value: object) -> list[dict[str, Any]]:
    """Coerce model proposals → bounded list of valid, known-action entries (unknown dropped)."""
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).strip()
        if action not in _AGENT_APPLY_ACTIONS:  # unknown / destructive → dropped
            continue
        params = item.get("params")
        profile_val = item.get("profile")
        out.append(
            {
                "action": action,
                "profile": (
                    profile_val.strip()
                    if isinstance(profile_val, str) and profile_val.strip()
                    else None
                ),
                "params": params if isinstance(params, dict) else {},
                "rationale": str(item.get("rationale", "")).strip()[:_AGENT_RATIONALE_MAX],
            }
        )
        if len(out) >= _AGENT_MAX_PROPOSALS:
            break
    return out


def _parse_agent_response(raw: str) -> dict[str, Any]:
    """Best-effort extraction of ``{reply, proposals[], suggestions[]}`` (never raises).

    Mirrors ``_parse_activity_summary``: any non-JSON / malformed output degrades to a plain
    ``reply`` carrying the whole text, so a confused model can't 500 the endpoint.
    """
    fallback = raw.strip()[:_AGENT_REPLY_MAX] if raw else ""
    empty: dict[str, Any] = {"reply": fallback, "proposals": [], "suggestions": []}
    if not raw:
        return empty
    text = raw.strip()
    candidates = [text]
    match = _SUMMARY_OBJECT_RE.search(text)
    if match is not None:
        candidates.append(match.group(0))
    data: object = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            break
    if not isinstance(data, dict):
        return empty
    reply = str(data.get("reply", "")).strip()[:_AGENT_REPLY_MAX] or fallback
    return {
        "reply": reply,
        "proposals": _parse_agent_proposals(data.get("proposals")),
        "suggestions": _clean_str_list(
            data.get("suggestions"),
            max_len=_AGENT_SUGGESTION_MAX,
            max_items=_AGENT_MAX_SUGGESTIONS,
        ),
    }


def _agent_list_preview(values: object, *, cap: int = _AGENT_LIST_CAP) -> str:
    """Comma-join a config list for the prompt, capped with a '[+N more]' suffix."""
    vals = [str(v) for v in values] if isinstance(values, (list, tuple)) else []
    if not vals:
        return "(none)"
    suffix = f" [+{len(vals) - cap} more]" if len(vals) > cap else ""
    return ", ".join(vals[:cap]) + suffix


def _agent_time_policy_line(rt: ProfileRuntime) -> str:
    """A one-line, human-readable summary of a profile's screen-time policy."""
    policy = rt.time_policy.current()
    if not policy.is_set():
        return "(no screen-time limits set)"
    if policy.source_text.strip():
        return policy.source_text.strip()[:300]
    default = policy.daily_minutes.get("default")
    return f"daily budget {default} min" if default is not None else "(custom limits set)"


def _agent_config_snapshot(runtimes: list[ProfileRuntime]) -> str:
    """Render the per-profile configuration snapshot injected into the system prompt."""
    blocks: list[str] = []
    for rt in runtimes:
        prompt_text = rt.prompt_store.current().strip()
        prompt_line = (
            (prompt_text[:200] + "…" if len(prompt_text) > 200 else prompt_text)
            if prompt_text
            else "(age-band default)"
        )
        blocks.append(
            f"Profile: {rt.name} (age {rt.age})\n"
            f"  Allowed sites (whitelist): {_agent_list_preview(rt.whitelist.current().values)}\n"
            f"  Blocked sites (blocklist): {_agent_list_preview(rt.blocklist.current().values)}\n"
            f"  Allowed search keywords: {_agent_list_preview(rt.search_allow.current().values)}\n"
            f"  Blocked search keywords: {_agent_list_preview(rt.search_block.current().values)}\n"
            f"  Screen-time policy: {_agent_time_policy_line(rt)}\n"
            f"  Prize balance: {rt.prize_point_store.balance()} points\n"
            f"  Custom classification prompt: {prompt_line}"
        )
    return "\n\n".join(blocks)


def _agent_activity_overview(events: list[dict[str, Any]]) -> str:
    """Compact verdict tally + top hosts across the activity slice."""
    verdicts: Counter[str] = Counter()
    hosts: Counter[str] = Counter()
    for ev in events:
        verdicts[str(ev.get("event") or "?")] += 1
        url = str(ev.get("url") or ev.get("url_key") or "")
        host = extract_host(url) if url else ""
        if host:
            hosts[host] += 1
    vparts = ", ".join(f"{k}={n}" for k, n in verdicts.most_common())
    hparts = ", ".join(f"{h}({n})" for h, n in hosts.most_common(10))
    return f"Verdict totals: {vparts or '(none)'}\nTop hosts: {hparts or '(none)'}"


def _agent_activity_text(events: list[dict[str, Any]], ages: dict[str, int]) -> str:
    """The activity section: a verdict/host overview plus the per-profile event digest."""
    if not events:
        return "(no recent activity)"
    overview = _agent_activity_overview(events)
    digest = _activity_digest(events, ages, max_lines=_AGENT_ACTIVITY_MAX_LINES)
    return f"{overview}\n\n{digest}"


def _build_agent_system_prompt(
    runtimes: list[ProfileRuntime], activity_text: str, versions: dict[str, Any]
) -> str:
    """Assemble the full system prompt: static header + stack + config snapshot + activity."""
    version_line = (
        f"Guardian v{versions.get('guardian')} | Extension v{versions.get('extension')} | "
        f"Model: {versions.get('model')}"
    )
    return (
        f"{_AGENT_SYSTEM_HEADER}\n\n"
        f"=== STACK ===\n{version_line}\n\n"
        f"=== CURRENT CONFIGURATION ===\n{_agent_config_snapshot(runtimes)}\n\n"
        f"=== RECENT ACTIVITY ===\n{activity_text}"
    )


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _response(
    verdict: str,
    reason: str,
    confidence: float,
    categories: list[str],
    url_key: str,
    cached: bool,
    duration_ms: int,
) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "reason": reason,
        "confidence": confidence,
        "categories_matched": categories,
        "url_key": url_key,
        "cached": cached,
        "duration_ms": duration_ms,
    }


def _build_runtimes(
    config: GuardianConfig,
    registry: ProfileRegistry | None,
    runtimes: dict[str, ProfileRuntime] | None,
    *,
    cache: VerdictCache | None,
    whitelist: WhitelistStore | None,
    request_store: RequestStore | None,
) -> dict[str, ProfileRuntime]:
    """Resolve per-profile stores. Precedence: injected runtimes > registry > single default.

    The single-default branch wraps the injected (or config-path) stores, so existing
    single-profile callers and tests are byte-identical.
    """
    if runtimes is not None:
        return runtimes
    if registry is not None:
        # build_runtime creates each teen's data dirs (incl. blocklist) and opens its stores.
        return {profile.name: build_runtime(profile) for profile in registry.all()}
    if not config.token:
        # Guard the public create_app() against a silent lockout: an empty default token
        # matches no request. (__main__ resolves the registry first, which also checks this.)
        raise ConfigError(
            "create_app: no registry/runtimes and GUARDIAN_TOKEN is empty — nothing could "
            "authenticate. Set GUARDIAN_TOKEN or pass a registry/runtimes."
        )
    base = Path(config.whitelist_path).expanduser().parent
    default = ProfileRuntime(
        name=DEFAULT_PROFILE_NAME,
        token=config.token,
        whitelist=whitelist or WhitelistStore(config.whitelist_path),
        blocklist=BlocklistStore(config.blocklist_path),
        request_store=request_store or RequestStore(config.requests_path),
        cache=cache or VerdictCache(config.cache_path),
        prompt_store=PromptStore(config.prompt_path),
        search_allow=KeywordStore(config.search_allow_path),
        search_block=KeywordStore(config.search_block_path),
        time_policy=TimePolicyStore(str(base / "time_policy.json")),
        time_request_store=TimeRequestStore(str(base / "time_requests.json")),
        prize_point_store=PrizePointStore(str(base / "prize_points.json")),
        age=DEFAULT_AGE,
    )
    return {default.name: default}


def create_app(
    config: GuardianConfig | None = None,
    *,
    classifier: Classifier | None = None,
    cache: VerdictCache | None = None,
    event_log: EventLog | None = None,
    summary_log: EventLog | None = None,
    metrics: GuardianMetrics | None = None,
    whitelist: WhitelistStore | None = None,
    request_store: RequestStore | None = None,
    registry: ProfileRegistry | None = None,
    runtimes: dict[str, ProfileRuntime] | None = None,
    pin_store: PinStore | None = None,
    manager: ProfileManager | None = None,
    time_ledger: TimeLedger | None = None,
) -> Starlette:
    """Build the guardian app. Dependencies may be injected for testing.

    One backend can serve several teen profiles: each request's ``X-Guardian-Token`` resolves
    to that teen's isolated whitelist, access-request store, and verdict cache. With no
    registry/runtimes a single ``"default"`` profile wraps the injected/config-path stores.
    """
    config = config or GuardianConfig.from_env()
    classifier = classifier or Classifier(config)
    event_log = event_log or EventLog(config.event_log_path)
    summary_log = summary_log or EventLog(config.summary_log_path)
    # Screen-time accounting is event-sourced over the same event log (dwell + time_grant).
    time_ledger = time_ledger or TimeLedger(event_log, tz=config.household_tz)
    metrics = metrics or GuardianMetrics()
    # The PIN lives in the store (hash file written by /setup), with the env PIN as fallback,
    # so a PIN created at runtime takes effect without restarting (config is frozen at startup).
    pin_store = pin_store or PinStore(config.admin_path, env_pin=config.parent_pin)
    # The manager owns the live name->runtime map (read via _pm.snapshot()) and persists
    # profile lifecycle changes. When not injected, build it from the same runtimes the
    # service has always used, plus the registry's profile records (paths) for persistence.
    if manager is None:
        profile_runtimes = _build_runtimes(
            config,
            registry,
            runtimes,
            cache=cache,
            whitelist=whitelist,
            request_store=request_store,
        )
        profile_records = {p.name: p for p in registry.all()} if registry is not None else {}
        manager = ProfileManager(
            profile_records, profile_runtimes, profiles_path=config.profiles_path
        )
    _pm = manager

    def _resolve_runtime(request: Request) -> ProfileRuntime | None:
        """Map ``X-Guardian-Token`` to its teen profile, or None to reject.

        Compares against every profile's token with ``hmac.compare_digest`` (selecting after
        the loop, not on first hit) so a near-correct token can't be confirmed byte-by-byte by
        timing. An empty/absent token is rejected up front.
        """
        token = request.headers.get("X-Guardian-Token", "")
        if not token:
            return None
        match: ProfileRuntime | None = None
        for runtime in _pm.snapshot().values():
            if hmac.compare_digest(runtime.token, token):
                match = runtime
        return match

    def _require_pin(request: Request) -> JSONResponse | None:
        """Gate parent-only endpoints behind the parent PIN (never sent to the extension).

        Reads through ``pin_store`` (hash file, else env PIN), so a PIN created via /setup
        applies immediately. ``verify`` is constant-time.
        """
        if not pin_store.is_configured():
            return JSONResponse({"error": "parent PIN not configured"}, status_code=503)
        if not pin_store.verify(request.headers.get("X-Guardian-Parent-Pin", "")):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return None

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def version_endpoint(request: Request) -> JSONResponse:
        """Return the deployed stack versions (parent-only; also fed to the Agent's context)."""
        guard = _require_pin(request)
        if guard is not None:
            return guard
        return JSONResponse(_stack_versions(config.agent_model))

    async def agent_chat(request: Request) -> JSONResponse:
        """Conversational guardian assistant: assemble context, ask the model, return the envelope.

        Read-only over all stores — the model proposes config changes (applied via /agent/apply),
        never writes here. PIN-gated; parsing is fail-safe; the conversation is stateless
        server-side (the client re-sends the bounded history each turn).
        """
        guard = _require_pin(request)
        if guard is not None:
            return guard
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        messages = _coerce_chat_messages(body.get("messages"))
        if not messages or messages[-1]["role"] != "user":
            return JSONResponse(
                {"error": "messages must be a non-empty list ending with a user message"},
                status_code=422,
            )
        # ``profile`` may arrive as a name, "", or JSON null (the "all profiles" scope the UI
        # sends). Normalize anything non-string — including null — to None; never str(None),
        # which would become the literal "None" and 404 as an unknown profile.
        profile_raw = body.get("profile")
        profile_name = (
            profile_raw.strip() if isinstance(profile_raw, str) and profile_raw.strip() else None
        )
        snap = _pm.snapshot()
        if profile_name is not None and profile_name not in snap:
            return JSONResponse({"error": "unknown profile"}, status_code=404)
        if profile_name is not None:
            runtimes = [snap[profile_name], _pm.global_runtime()]
        else:
            runtimes = [*snap.values(), _pm.global_runtime()]
        loop = asyncio.get_running_loop()
        events = await loop.run_in_executor(
            None,
            functools.partial(
                event_log.recent,
                _AGENT_ACTIVITY_LIMIT,
                profile=profile_name,
                events=ACTIVITY_EVENTS,
            ),
        )
        ages = {rt.name: rt.age for rt in snap.values()}
        system_prompt = _build_agent_system_prompt(
            runtimes,
            _agent_activity_text(events, ages),
            _stack_versions(config.agent_model),
        )
        try:
            raw = await asyncio.wait_for(
                classifier.generate(
                    system_prompt=system_prompt,
                    user_prompt=_render_conversation(messages),
                    model=config.agent_model,
                ),
                timeout=config.classify_timeout_s,
            )
        except Exception:  # noqa: BLE001 - any SDK/transport/timeout error → friendly 502
            return JSONResponse({"error": "agent chat failed"}, status_code=502)
        result = _parse_agent_response(raw)
        event_log.log("agent_chat", profile=profile_name or "", proposals=len(result["proposals"]))
        return JSONResponse(result)

    async def classify(request: Request) -> JSONResponse:
        rt = _resolve_runtime(request)
        if rt is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)

        url = str(body.get("url", ""))
        url_key = str(body.get("url_key") or normalize_url(url))
        host = extract_host(url_key)
        can_escalate = bool(body.get("can_escalate", True))
        screenshot = body.get("screenshot_b64")
        start = time.monotonic()
        loop = asyncio.get_running_loop()

        # Hot-reload this teen's lists + prompt and the shared Global lists + prompt; any change
        # invalidates the teen's cached verdicts (Global edits are picked up here lazily, per teen).
        gl = _pm.global_runtime()
        changed = False
        for reloader in (
            rt.whitelist.reload_if_changed,
            rt.blocklist.reload_if_changed,
            rt.prompt_store.reload_if_changed,
            rt.search_allow.reload_if_changed,
            rt.search_block.reload_if_changed,
            gl.whitelist.reload_if_changed,
            gl.blocklist.reload_if_changed,
            gl.prompt_store.reload_if_changed,
            gl.search_allow.reload_if_changed,
            gl.search_block.reload_if_changed,
        ):
            changed = await loop.run_in_executor(None, reloader) or changed
        if changed:
            await loop.run_in_executor(None, rt.cache.clear)

        wl, bl = rt.whitelist.current(), rt.blocklist.current()
        gwl, gbl = gl.whitelist.current(), gl.blocklist.current()

        # Hard URL rules: authoritative, checked before the cache (classifier skipped).
        # Individual always wins: kid block, then kid allow, then Global block, Global allow.
        if bl.matches_url(url):
            event_log.log("blocklist_block", url=url, url_key=url_key, profile=rt.name)
            metrics.record_classification("block", (), 0, host)
            return JSONResponse(_response("block", "blocklisted", 1.0, [], url_key, False, 0))
        if wl.matches_url(url):
            event_log.log("whitelist_allow", url=url, url_key=url_key, profile=rt.name)
            metrics.record_whitelist_hit(host)
            return JSONResponse(_response("allow", "whitelisted", 1.0, [], url_key, False, 0))
        if gbl.matches_url(url):
            event_log.log(
                "blocklist_block", url=url, url_key=url_key, profile=rt.name, scope="global"
            )
            metrics.record_classification("block", (), 0, host)
            return JSONResponse(
                _response("block", "blocklisted_global", 1.0, [], url_key, False, 0)
            )
        if gwl.matches_url(url):
            event_log.log(
                "whitelist_allow", url=url, url_key=url_key, profile=rt.name, scope="global"
            )
            metrics.record_whitelist_hit(host)
            return JSONResponse(
                _response("allow", "whitelisted_global", 1.0, [], url_key, False, 0)
            )

        cached = await loop.run_in_executor(None, rt.cache.get, url_key)
        if cached is not None:
            event_log.log(
                "cache_hit", url=url, url_key=url_key, verdict=cached.verdict, profile=rt.name
            )
            metrics.record_cache_hit(host)
            return JSONResponse(
                _response(cached.verdict, cached.reason, cached.confidence, [], url_key, True, 0)
            )

        try:
            verdict: Verdict = await asyncio.wait_for(
                classifier.classify(
                    body,
                    screenshot_b64=screenshot,
                    age=rt.age,
                    policy=_pm.merged_policy(rt),
                    approved_topics=(*wl.content_entries, *gwl.content_entries),
                    disallowed_topics=(*bl.content_entries, *gbl.content_entries),
                ),
                timeout=config.classify_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 - fail-open on timeout or any error
            event_log.log(
                "fail_open", url=url, url_key=url_key, reason=type(exc).__name__, profile=rt.name
            )
            metrics.record_fail_open(host)
            return JSONResponse(
                _response(
                    "allow", "classification_unavailable", 0.0, [], url_key, False, _ms(start)
                )
            )

        # Escalate to a screenshot when the text verdict is inconclusive (first call only).
        low_confidence = verdict.confidence < config.screenshot_confidence_threshold
        if (
            verdict.verdict != "block"
            and (verdict.verdict == "need_screenshot" or low_confidence)
            and can_escalate
            and not screenshot
        ):
            event_log.log(
                "escalate", url=url, url_key=url_key, confidence=verdict.confidence, profile=rt.name
            )
            return JSONResponse(
                _response(
                    "need_screenshot",
                    verdict.reason,
                    verdict.confidence,
                    list(verdict.categories),
                    url_key,
                    False,
                    _ms(start),
                )
            )

        # Resolve to a concrete allow/block (need_screenshot with no further escalation => allow).
        final = verdict.verdict if verdict.verdict == "block" else "allow"
        duration = _ms(start)
        await loop.run_in_executor(
            None, rt.cache.put, url_key, final, verdict.reason, verdict.confidence
        )
        event_log.log(
            final,
            url=url,
            url_key=url_key,
            verdict=final,
            reason=verdict.reason,
            confidence=verdict.confidence,
            categories=list(verdict.categories),
            had_screenshot=bool(screenshot),
            duration_ms=duration,
            profile=rt.name,
        )
        metrics.record_classification(final, verdict.categories, duration, host)
        return JSONResponse(
            _response(
                final,
                verdict.reason,
                verdict.confidence,
                list(verdict.categories),
                url_key,
                False,
                duration,
            )
        )

    async def search_classify(request: Request) -> JSONResponse:
        """Classify a bare search query (token-authed): parent keyword lists, then age-aware AI.

        Mirrors classify(): parent lists are checked synchronously, the AI verdict is cached
        under a ``search:`` key, and any error/timeout fails open (allow) so a backend hiccup
        never blocks all searching.
        """
        rt = _resolve_runtime(request)
        if rt is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        query = str(body.get("query", "")).strip()
        if not query or len(query) > _MAX_QUERY_CHARS:
            return JSONResponse(
                {"error": f"query required (1-{_MAX_QUERY_CHARS} chars)"}, status_code=422
            )
        loop = asyncio.get_running_loop()

        # Hot-reload this teen's + Global's search lists and prompt; any change clears the teen's
        # cached verdicts (search verdicts share the cache under a "search:" key prefix).
        gl = _pm.global_runtime()
        changed = False
        for reloader in (
            rt.search_allow.reload_if_changed,
            rt.search_block.reload_if_changed,
            rt.prompt_store.reload_if_changed,
            gl.search_allow.reload_if_changed,
            gl.search_block.reload_if_changed,
            gl.prompt_store.reload_if_changed,
        ):
            changed = await loop.run_in_executor(None, reloader) or changed
        if changed:
            await loop.run_in_executor(None, rt.cache.clear)

        cache_key = "search:" + query.lower()[:_MAX_QUERY_CHARS]
        cached = await loop.run_in_executor(None, rt.cache.get, cache_key)
        if cached is not None:
            return JSONResponse(
                {"verdict": cached.verdict, "reason": cached.reason, "cached": True}
            )

        try:
            verdict = await asyncio.wait_for(
                classify_search_query(
                    query,
                    teen_allow=rt.search_allow.current(),
                    global_allow=gl.search_allow.current(),
                    teen_block=rt.search_block.current(),
                    global_block=gl.search_block.current(),
                    classifier=classifier,
                    age=rt.age,
                    policy=_pm.merged_policy(rt),
                ),
                timeout=config.classify_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 - fail-open on timeout or any error
            event_log.log("search_fail_open", reason=type(exc).__name__, profile=rt.name)
            return JSONResponse({"verdict": "allow", "reason": "classification_unavailable"})

        await loop.run_in_executor(
            None, rt.cache.put, cache_key, verdict.verdict, verdict.reason, verdict.confidence
        )
        # Never log the raw query (it may be sensitive); record only its length + verdict.
        event_log.log(
            "search_classify", query_len=len(query), verdict=verdict.verdict, profile=rt.name
        )
        return JSONResponse({"verdict": verdict.verdict, "reason": verdict.reason, "cached": False})

    async def dwell(request: Request) -> JSONResponse:
        rt = _resolve_runtime(request)
        if rt is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        url_key = str(body.get("url_key", "")).strip()
        dwell_ms = body.get("dwell_ms")
        if (
            not url_key
            or isinstance(dwell_ms, bool)
            or not isinstance(dwell_ms, (int, float))
            or dwell_ms < 0
        ):
            return JSONResponse(
                {"error": "url_key and non-negative dwell_ms required"}, status_code=422
            )
        host = extract_host(url_key)
        now = datetime.now(UTC)
        # Account before logging: the first touch of the day seeds from the log as it stands,
        # so counting this not-yet-logged event separately avoids a double count.
        time_ledger.add_dwell(rt.name, host, int(dwell_ms), now)
        metrics.record_dwell(host, rt.name, float(dwell_ms) / 1000.0)
        event_log.log("dwell", url_key=url_key, host=host, dwell_ms=int(dwell_ms), profile=rt.name)
        # Return the current time-state so the extension's heartbeat can enforce immediately.
        return JSONResponse({"ok": True, **_time_state(rt, url_key, now)})

    def _resolve_time_policy(rt: ProfileRuntime) -> TimePolicy:
        """Effective policy for a teen: its own, with the Global profile layered under it."""
        return resolve_time_policy(
            rt.time_policy.current(), _pm.global_runtime().time_policy.current()
        )

    def _time_state(rt: ProfileRuntime, url: str | None, now: datetime) -> dict[str, Any]:
        host = extract_host(url) if url else None
        usage = time_ledger.usage(rt.name, _resolve_time_policy(rt), host, now)
        return _usage_to_json(usage)

    async def time_state_endpoint(request: Request) -> JSONResponse:
        # Teen-facing: the extension reads its remaining credits + whether to block (token-authed).
        rt = _resolve_runtime(request)
        if rt is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        url = request.query_params.get("url", "").strip()
        return JSONResponse(_time_state(rt, url or None, datetime.now(UTC)))

    async def time_request_endpoint(request: Request) -> JSONResponse:
        # Teen-facing: ask a parent for more time (token-authed). Granting is PIN-gated below.
        rt = _resolve_runtime(request)
        if rt is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        loop = asyncio.get_running_loop()

        if request.method == "GET":
            target = request.query_params.get("target_host", "").strip() or None
            match = next(
                (
                    r
                    for r in reversed(rt.time_request_store.current().requests)
                    if r.target_host == target
                ),
                None,
            )
            if match is None:
                return JSONResponse({"status": "none"})
            return JSONResponse(
                {
                    "status": match.status,
                    "id": match.id,
                    "granted_minutes": match.granted_minutes,
                    "decision_note": match.decision_note,
                    "target_host": match.target_host,
                }
            )

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        reason = str(body.get("reason", "")).strip()
        note = str(body.get("note", "")).strip()
        if len(reason) > _MAX_TIME_REASON or len(note) > _MAX_TIME_REASON:
            return JSONResponse(
                {"error": f"reason and note must be at most {_MAX_TIME_REASON} chars"},
                status_code=422,
            )
        target_raw = str(body.get("target_host", "")).strip()
        target = canonicalize_url(target_raw).split("/", 1)[0] if target_raw else None
        minutes = _clamp_request_minutes(body.get("requested_minutes"))
        req = await loop.run_in_executor(
            None,
            functools.partial(
                rt.time_request_store.add_request,
                target_host=target,
                requested_minutes=minutes,
                reason=reason,
                note=note,
            ),
        )
        event_log.log(
            "time_request",
            id=req.id,
            profile=rt.name,
            target_host=target,
            requested_minutes=minutes,
        )
        return JSONResponse({"id": req.id, "status": req.status})

    async def whitelist_endpoint(request: Request) -> JSONResponse:
        rt = _resolve_runtime(request)
        if rt is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if request.method == "GET":
            entries = [
                {"value": value, "type": classify_entry(value)}
                for value in rt.whitelist.current().values
            ]
            return JSONResponse({"entries": entries})
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        entry = str(body.get("entry", "")).strip()
        if not entry or len(entry) > 512 or not entry.isprintable():
            return JSONResponse(
                {"error": "entry must be a non-empty, single-line string (max 512 chars)"},
                status_code=422,
            )
        loop = asyncio.get_running_loop()
        try:
            if request.method == "POST":
                await loop.run_in_executor(None, rt.whitelist.add, entry)
            else:  # DELETE
                await loop.run_in_executor(None, rt.whitelist.remove, entry)
            # A whitelist change invalidates cached verdicts.
            await loop.run_in_executor(None, rt.cache.clear)
        except OSError:
            return JSONResponse({"error": "whitelist write failed"}, status_code=500)
        event_log.log(f"whitelist_{request.method.lower()}", entry=entry, profile=rt.name)
        if request.method == "POST":
            return JSONResponse({"value": entry, "type": classify_entry(entry)})
        return JSONResponse({"ok": True})

    async def access_request_endpoint(request: Request) -> JSONResponse:
        # Teen-facing: the extension already holds X-Guardian-Token, so submitting/checking a
        # request is low-privilege. Approving is NOT here — that needs the parent PIN (below).
        rt = _resolve_runtime(request)
        if rt is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        loop = asyncio.get_running_loop()

        if request.method == "GET":
            url = request.query_params.get("url", "").strip()
            if not url:
                return JSONResponse({"error": "url query param required"}, status_code=422)
            match = rt.request_store.current().latest_for_url_key(normalize_url(url))
            if match is None:
                return JSONResponse({"status": "none"})
            return JSONResponse(
                {
                    "status": match.status,
                    "id": match.id,
                    "decision_note": match.decision_note,
                    "whitelist_entry": match.whitelist_entry,
                }
            )

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        url = str(body.get("url", "")).strip()
        note = str(body.get("note", "")).strip()
        reason = str(body.get("reason", "")).strip()
        if not url or len(url) > 2048 or not url.startswith(("http://", "https://")):
            return JSONResponse(
                {"error": "url must be an http(s) URL (max 2048 chars)"}, status_code=422
            )
        if len(note) > 500 or len(reason) > 500:
            return JSONResponse(
                {"error": "note and reason must be at most 500 chars"}, status_code=422
            )
        url_key = normalize_url(url)
        host = extract_host(url_key)
        req = await loop.run_in_executor(
            None,
            functools.partial(
                rt.request_store.add_request,
                url=url,
                url_key=url_key,
                host=host,
                reason=reason,
                note=note,
            ),
        )
        event_log.log(
            "access_request", url=url, url_key=url_key, host=host, id=req.id, profile=rt.name
        )
        metrics.record_access_request(host)
        return JSONResponse({"id": req.id, "status": req.status})

    async def search_request_endpoint(request: Request) -> JSONResponse:
        # Teen-facing: ask a parent to allow a blocked search keyword (token-authed, low-privilege).
        rt = _resolve_runtime(request)
        if rt is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        loop = asyncio.get_running_loop()

        if request.method == "GET":
            query = request.query_params.get("query", "").strip()
            if not query or len(query) > _MAX_QUERY_CHARS:
                return JSONResponse({"error": "query param required"}, status_code=422)
            match = rt.request_store.current().latest_for_keyword(query)
            if match is None:
                return JSONResponse({"status": "none"})
            return JSONResponse(
                {"status": match.status, "id": match.id, "decision_note": match.decision_note}
            )

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        query = str(body.get("query", "")).strip()
        page_url = str(body.get("url", "")).strip()
        note = str(body.get("note", "")).strip()
        if not query or len(query) > _MAX_QUERY_CHARS:
            return JSONResponse(
                {"error": f"query required (1-{_MAX_QUERY_CHARS} chars)"}, status_code=422
            )
        if not page_url or len(page_url) > 2048 or not page_url.startswith(("http://", "https://")):
            return JSONResponse({"error": "url must be an http(s) URL"}, status_code=422)
        if len(note) > 500:
            return JSONResponse({"error": "note must be at most 500 chars"}, status_code=422)
        url_key = normalize_url(page_url)
        req = await loop.run_in_executor(
            None,
            functools.partial(
                rt.request_store.add_request,
                url=page_url,
                url_key=url_key,
                host=extract_host(url_key),
                reason=f"Blocked search: {query[:200]}",
                note=note,
                kind="search",
                keyword=query,
            ),
        )
        # Log only the keyword length; the parent reviews the keyword itself in the stored request.
        event_log.log("search_request", query_len=len(query), id=req.id, profile=rt.name)
        return JSONResponse({"id": req.id, "status": req.status})

    async def home_page(_request: Request) -> Response:
        # The parent app shell. On first run there is no PIN and nothing to show, so route to
        # the setup wizard. The redirect is server-side so it holds even with JS disabled.
        if not pin_store.is_configured():
            return RedirectResponse("/setup", status_code=302)
        return FileResponse(Path(__file__).parent / "home.html", media_type="text/html")

    async def setup_page(_request: Request) -> Response:
        # First-run wizard. No auth: there is no PIN/token to present yet (like /health).
        # Once a PIN exists there is nothing to set up — send the parent to the shell.
        if pin_store.is_configured():
            return RedirectResponse("/", status_code=302)
        return FileResponse(Path(__file__).parent / "setup.html", media_type="text/html")

    async def setup_status(_request: Request) -> JSONResponse:
        # Lets the wizard detect first run on load. Leaks only whether a PIN exists, nothing else.
        return JSONResponse({"pin_configured": pin_store.is_configured()})

    async def setup_pin(request: Request) -> JSONResponse:
        # One-shot: once a PIN exists this is closed (409), so it can't reset an existing PIN.
        if pin_store.is_configured():
            return JSONResponse({"error": "parent PIN already configured"}, status_code=409)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        pin = str(body.get("pin", "")).strip()
        error = validate_pin_format(pin)
        if error is not None:
            return JSONResponse({"error": error}, status_code=422)
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, pin_store.set_pin, pin)
        except OSError:
            return JSONResponse({"error": "could not save the PIN"}, status_code=500)
        event_log.log("parent_pin_set")  # records the event, never the PIN value
        return JSONResponse({"ok": True})

    async def review_page(_request: Request) -> RedirectResponse:
        # Folded into the app shell: keep the /review bookmark working by routing into the
        # Requests section. The "#/requests" fragment is read client-side; the server sees "/".
        return RedirectResponse("/#/requests", status_code=302)

    async def review_requests(request: Request) -> JSONResponse:
        guard = _require_pin(request)
        if guard is not None:
            return guard
        # One parent reviews every teen: aggregate across profiles, labelling each request
        # with the teen it belongs to so approvals can be routed back to the right whitelist.
        pending: list[dict[str, Any]] = []
        recent: list[dict[str, Any]] = []
        for runtime in _pm.snapshot().values():
            snapshot = runtime.request_store.current()
            pending.extend({**asdict(r), "profile": runtime.name} for r in snapshot.pending())
            recent.extend({**asdict(r), "profile": runtime.name} for r in snapshot.recent_decided())
        pending.sort(key=lambda r: r["created_ts"])
        recent.sort(key=lambda r: r.get("decided_ts") or "", reverse=True)
        return JSONResponse({"pending": pending, "recent": recent[:50]})

    async def review_decision(request: Request) -> JSONResponse:
        guard = _require_pin(request)
        if guard is not None:
            return guard
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        request_id = str(body.get("id", "")).strip()
        decision = str(body.get("decision", "")).strip()
        if not request_id or decision not in ("approve", "reject"):
            return JSONResponse(
                {"error": "id and decision (approve|reject) required"}, status_code=422
            )
        note = str(body.get("note", "")).strip() or None
        # Optional "block similar content" rule applied only on reject (see _apply_block_rule):
        # a free-text classifier rule and/or a hard list entry, scoped to this teen or Global.
        block_rule = str(body.get("block_rule", "")).strip() or None
        block_hard = bool(body.get("block_hard", False))
        block_scope = (
            "global" if str(body.get("block_scope", "")).strip() == "global" else "profile"
        )
        loop = asyncio.get_running_loop()

        # Find which teen owns this request (ids are globally unique uuid4); the decision and its
        # side effects apply only to that teen's stores, never another's.
        owner: ProfileRuntime | None = None
        existing = None
        for runtime in _pm.snapshot().values():
            match = runtime.request_store.current().by_id(request_id)
            if match is not None:
                owner = runtime
                existing = match
                break
        if owner is None or existing is None:
            return JSONResponse({"error": "request not found"}, status_code=404)

        # A search-keyword request approves the keyword onto the teen's search ALLOW list (not the
        # URL whitelist); the keyword is fixed at request time, so no parent-supplied entry.
        if existing.kind == "search":
            try:
                decided = await loop.run_in_executor(
                    None,
                    functools.partial(
                        owner.request_store.decide,
                        request_id,
                        decision=decision,
                        whitelist_entry=existing.keyword if decision == "approve" else None,
                        decision_note=note,
                    ),
                )
            except KeyError:
                return JSONResponse({"error": "request not found"}, status_code=404)
            except ValueError:
                return JSONResponse({"error": "request already decided"}, status_code=422)
            if decision == "approve" and existing.keyword:
                try:
                    await loop.run_in_executor(None, owner.search_allow.add, existing.keyword)
                    await loop.run_in_executor(None, owner.cache.clear)
                except OSError:
                    return JSONResponse({"error": "keyword write failed"}, status_code=500)
                event_log.log("search_request_approved", id=request_id, profile=owner.name)
                metrics.record_access_decision("approve")
                return JSONResponse({"id": decided.id, "status": decided.status})
            event_log.log("search_request_rejected", id=request_id, profile=owner.name)
            metrics.record_access_decision("reject")
            applied_rule, applied_hard = await _apply_block_rule(
                owner,
                scope=block_scope,
                rule=block_rule,
                hard=block_hard,
                kind="search",
                existing=existing,
            )
            return JSONResponse(
                {
                    "id": decided.id,
                    "status": decided.status,
                    "rule_applied": applied_rule,
                    "hard_block_applied": applied_hard,
                }
            )

        # The whitelist entry is the parent's choice; default to the request's RAW url (an exact
        # match) — never the url_key, which collapses YouTube videos to a non-matching topic.
        entry = str(body.get("whitelist_entry", "")).strip()
        if decision == "approve" and not entry:
            entry = existing.url
        # The entry is added to the whitelist (and content entries are injected verbatim into the
        # classifier prompt), so apply the same guard as POST /whitelist: single line, bounded.
        if entry and (len(entry) > 512 or not entry.isprintable()):
            return JSONResponse(
                {"error": "whitelist entry must be a single-line string (max 512 chars)"},
                status_code=422,
            )

        try:
            decided = await loop.run_in_executor(
                None,
                functools.partial(
                    owner.request_store.decide,
                    request_id,
                    decision=decision,
                    whitelist_entry=entry if decision == "approve" else None,
                    decision_note=note,
                ),
            )
        except KeyError:
            return JSONResponse({"error": "request not found"}, status_code=404)
        except ValueError:
            return JSONResponse({"error": "request already decided"}, status_code=422)

        if decision == "approve":
            try:
                await loop.run_in_executor(None, owner.whitelist.add, entry)
                await loop.run_in_executor(None, owner.cache.clear)  # an allow invalidates a block
            except OSError:
                return JSONResponse({"error": "whitelist write failed"}, status_code=500)
            event_log.log("access_request_approved", id=request_id, entry=entry, profile=owner.name)
            metrics.record_access_decision("approve")
            return JSONResponse({"id": decided.id, "status": decided.status})
        event_log.log("access_request_rejected", id=request_id, profile=owner.name)
        metrics.record_access_decision("reject")
        applied_rule, applied_hard = await _apply_block_rule(
            owner,
            scope=block_scope,
            rule=block_rule,
            hard=block_hard,
            kind=existing.kind,
            existing=existing,
        )
        return JSONResponse(
            {
                "id": decided.id,
                "status": decided.status,
                "rule_applied": applied_rule,
                "hard_block_applied": applied_hard,
            }
        )

    def _time_policy_store_for(name: str) -> TimePolicyStore | None:
        if name.lower() == GLOBAL_PROFILE_NAME:
            return _pm.global_runtime().time_policy
        rt = _pm.snapshot().get(name)
        return rt.time_policy if rt is not None else None

    async def review_time_requests(request: Request) -> JSONResponse:
        # Parent-facing (PIN): pending + recently decided "more time" asks across all teens.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        pending: list[dict[str, Any]] = []
        recent: list[dict[str, Any]] = []
        for rt in _pm.snapshot().values():
            snap = rt.time_request_store.current()
            for r in snap.pending():
                pending.append({**asdict(r), "profile": rt.name, "age": rt.age})
            for r in snap.recent_decided():
                recent.append({**asdict(r), "profile": rt.name})
        pending.sort(key=lambda r: r["created_ts"])
        recent.sort(key=lambda r: r.get("decided_ts") or "", reverse=True)
        return JSONResponse({"pending": pending, "recent": recent[:50]})

    async def review_time_decision(request: Request) -> JSONResponse:
        # Parent-facing (PIN): grant bonus minutes (approve) or deny a time request.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        request_id = str(body.get("id", "")).strip()
        profile = str(body.get("profile", "")).strip()
        decision = str(body.get("decision", "")).strip()
        if decision not in ("approve", "reject"):
            return JSONResponse({"error": "decision must be approve|reject"}, status_code=422)
        owner = _pm.snapshot().get(profile)
        if owner is None:
            return JSONResponse({"error": "unknown profile"}, status_code=404)
        granted = _clamp_request_minutes(body.get("granted_minutes"))
        if decision == "approve" and granted is None:
            return JSONResponse(
                {"error": f"granted_minutes must be an integer 1..{_MAX_REQUEST_MINUTES}"},
                status_code=422,
            )
        raw_note = body.get("note")
        note = raw_note.strip() if isinstance(raw_note, str) else None
        loop = asyncio.get_running_loop()
        try:
            decided = await loop.run_in_executor(
                None,
                functools.partial(
                    owner.time_request_store.decide,
                    request_id,
                    decision=decision,
                    granted_minutes=granted,
                    decision_note=note,
                ),
            )
        except KeyError:
            return JSONResponse({"error": "request not found"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        if decision == "approve" and granted:
            now = datetime.now(UTC)
            # A grant extends today's general pool (event-sourced; expires at day rollover).
            time_ledger.add_grant(owner.name, granted, now)
            event_log.log(
                "time_grant",
                profile=owner.name,
                minutes=granted,
                target_host=decided.target_host,
                request_id=decided.id,
            )
        return JSONResponse(
            {
                "id": decided.id,
                "status": decided.status,
                "granted_minutes": decided.granted_minutes,
            }
        )

    async def time_policy_endpoint(request: Request) -> JSONResponse:
        # Parent-facing (PIN): read / replace a profile's (or Global's) structured time policy.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        name = request.query_params.get("profile", "").strip()
        store = _time_policy_store_for(name)
        if store is None:
            return JSONResponse({"error": "unknown profile"}, status_code=404)
        is_global = name.lower() == GLOBAL_PROFILE_NAME
        if request.method == "GET":
            own = store.current()
            glob = _pm.global_runtime().time_policy.current()
            effective = own if is_global else resolve_time_policy(own, glob)
            return JSONResponse(
                {
                    "profile": name,
                    "is_global": is_global,
                    "policy": time_policy_to_json(own),
                    "effective": time_policy_to_json(effective),
                }
            )
        # PUT: replace with the structured policy (validated + clamped), stamped server-side.
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        if not isinstance(body, dict):
            return JSONResponse({"error": "policy object required"}, status_code=422)
        source = body.get("source_text")
        if isinstance(source, str) and len(source) > _MAX_TIME_TEXT:
            return JSONResponse({"error": "source_text too long"}, status_code=422)
        body["updated_ts"] = datetime.now(UTC).isoformat()
        policy = time_policy_from_stored(body)
        loop = asyncio.get_running_loop()
        try:
            saved = await loop.run_in_executor(None, store.set, policy)
        except OSError:
            return JSONResponse({"error": "policy write failed"}, status_code=500)
        event_log.log("time_policy_set", profile=name or DEFAULT_PROFILE_NAME)
        return JSONResponse({"ok": True, "policy": time_policy_to_json(saved)})

    async def time_policy_parse(request: Request) -> JSONResponse:
        # Parent-facing (PIN): turn natural-language limits into a structured policy (preview only).
        guard = _require_pin(request)
        if guard is not None:
            return guard
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        text = str(body.get("text", "")).strip()
        if not text or len(text) > _MAX_TIME_TEXT:
            return JSONResponse(
                {"error": f"text required (max {_MAX_TIME_TEXT} chars)"}, status_code=422
            )
        try:
            raw = await asyncio.wait_for(
                classifier.generate(system_prompt=_TIME_POLICY_SYSTEM_PROMPT, user_prompt=text),
                timeout=config.classify_timeout_s,
            )
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "time policy parse failed"}, status_code=502)
        policy = parse_time_policy(raw, source_text=text)
        return JSONResponse({"policy": time_policy_to_json(policy)})

    async def review_time_usage(request: Request) -> JSONResponse:
        # Parent-facing (PIN): each teen's today used/remaining for the dashboard live view.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        now = datetime.now(UTC)
        glob = _pm.global_runtime().time_policy.current()
        out: list[dict[str, Any]] = []
        for rt in _pm.snapshot().values():
            policy = resolve_time_policy(rt.time_policy.current(), glob)
            usage = time_ledger.usage(rt.name, policy, None, now)
            out.append(
                {
                    "profile": rt.name,
                    "age": rt.age,
                    "has_policy": policy.is_set(),
                    "general": {
                        "used_ms": usage.general_used_ms,
                        "limit_ms": usage.general_limit_ms,
                        "remaining_ms": usage.general_remaining_ms,
                        "blocked": usage.blocked_general,
                    },
                    "bedtime_active": usage.bedtime_active,
                }
            )
        return JSONResponse({"profiles": out})

    def _prize_state(rt: ProfileRuntime, now: datetime) -> dict[str, Any]:
        """A teen's prize balance + which redemption packages they can afford right now."""
        balance = rt.prize_point_store.balance()
        start, end = time_ledger.day_bounds_utc(now)
        redeemed = redeemed_minutes_today(event_log, rt.name, start=start, end=end)
        remaining_cap = max(0, config.prize_daily_bonus_cap_min - redeemed)
        return {
            "balance": balance,
            "points_per_minute": POINTS_PER_MINUTE,
            "daily_cap_min": config.prize_daily_bonus_cap_min,
            "remaining_daily_bonus_min": remaining_cap,
            "packages": [
                {
                    "minutes": minutes,
                    "cost": cost_for_minutes(minutes),
                    "affordable": (
                        balance >= cost_for_minutes(minutes) and minutes <= remaining_cap
                    ),
                }
                for minutes in REDEEM_PACKAGES_MIN
            ],
        }

    async def prize_points_state(request: Request) -> JSONResponse:
        # Teen-facing (token): current balance + affordable packages for the popup/block page.
        rt = _resolve_runtime(request)
        if rt is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return JSONResponse(_prize_state(rt, datetime.now(UTC)))

    async def prize_points_redeem(request: Request) -> JSONResponse:
        # Teen-facing (token): spend points for bonus minutes — self-serve, no parent PIN.
        rt = _resolve_runtime(request)
        if rt is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        minutes = body.get("minutes")
        if (
            isinstance(minutes, bool)
            or not isinstance(minutes, int)
            or minutes not in REDEEM_PACKAGES_MIN
        ):
            return JSONResponse(
                {"error": f"minutes must be one of {list(REDEEM_PACKAGES_MIN)}"},
                status_code=422,
            )
        cost = cost_for_minutes(minutes)
        now = datetime.now(UTC)
        # Enforce the per-day bonus cap (independent of parent grants) before spending.
        start, end = time_ledger.day_bounds_utc(now)
        redeemed = redeemed_minutes_today(event_log, rt.name, start=start, end=end)
        remaining_cap = max(0, config.prize_daily_bonus_cap_min - redeemed)
        if minutes > remaining_cap:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "daily_cap_reached",
                    "balance": rt.prize_point_store.balance(),
                    "remaining_daily_bonus_min": remaining_cap,
                },
                status_code=409,
            )
        # Atomic spend: deduct only if the balance covers it (no double-click race).
        new_balance = rt.prize_point_store.try_spend(cost)
        if new_balance is None:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "insufficient_points",
                    "balance": rt.prize_point_store.balance(),
                },
                status_code=409,
            )
        # Grant the bonus time via the same seam a parent approval uses, then record it.
        time_ledger.add_grant(rt.name, minutes, now)
        event_log.log(
            "prize_points_redeemed",
            profile=rt.name,
            delta=-cost,
            points=cost,
            minutes_granted=minutes,
            balance_after=new_balance,
        )
        metrics.record_prize_redeem(rt.name, cost, new_balance)
        url = request.query_params.get("url", "").strip() or None
        return JSONResponse(
            {
                "ok": True,
                "granted_minutes": minutes,
                "balance": new_balance,
                **_time_state(rt, url, now),
            }
        )

    async def review_prize_points(request: Request) -> JSONResponse:
        # Parent-facing (PIN): each teen's balance + the redemption policy (Prize point page).
        guard = _require_pin(request)
        if guard is not None:
            return guard
        balances = [
            {"profile": rt.name, "balance": rt.prize_point_store.balance()}
            for rt in _pm.snapshot().values()
        ]
        return JSONResponse(
            {
                "points_per_minute": POINTS_PER_MINUTE,
                "daily_cap_min": config.prize_daily_bonus_cap_min,
                "packages": [
                    {"minutes": minutes, "cost": cost_for_minutes(minutes)}
                    for minutes in REDEEM_PACKAGES_MIN
                ],
                "balances": balances,
            }
        )

    async def review_prize_grant(request: Request) -> JSONResponse:
        # Parent-facing (PIN): award (or correct) a teen's prize points.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        profile = str(body.get("profile", "")).strip()
        points = _clamp_prize_points(body.get("points"))
        if points is None:
            return JSONResponse(
                {"error": f"points must be a non-zero integer within +-{_MAX_PRIZE_POINTS}"},
                status_code=422,
            )
        owner = _pm.snapshot().get(profile)
        if owner is None:
            return JSONResponse({"error": "unknown profile"}, status_code=404)
        raw_reason = body.get("reason")
        reason = raw_reason.strip()[:200] if isinstance(raw_reason, str) else ""
        balance = owner.prize_point_store.add(points)
        event_log.log(
            "prize_points_earned",
            profile=owner.name,
            delta=points,
            reason=reason,
            balance_after=balance,
        )
        metrics.record_prize_grant(owner.name, points, balance)
        return JSONResponse({"ok": True, "profile": owner.name, "balance": balance})

    async def review_prize_events(request: Request) -> JSONResponse:
        # Parent-facing (PIN): the prize-point event feed for the Activity "Prize points" tab.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        profile = request.query_params.get("profile", "").strip() or None
        limit = _parse_activity_limit(request.query_params.get("limit"))
        loop = asyncio.get_running_loop()
        events = await loop.run_in_executor(
            None,
            functools.partial(event_log.recent, limit, profile=profile, events=PRIZE_EVENTS),
        )
        return JSONResponse({"events": events})

    async def review_suggest_block_rule(request: Request) -> JSONResponse:
        # Optional aid for the reject flow: draft a natural-language "block similar content"
        # rule from a request's details. PIN-gated and READ-ONLY (no store mutation); strictly
        # best-effort, so any failure here returns an error the UI can shrug off — it never
        # blocks the guardian from rejecting.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        request_id = str(body.get("id", "")).strip()
        if not request_id:
            return JSONResponse({"error": "id required"}, status_code=422)

        owner: ProfileRuntime | None = None
        existing = None
        for runtime in _pm.snapshot().values():
            match = runtime.request_store.current().by_id(request_id)
            if match is not None:
                owner = runtime
                existing = match
                break
        if owner is None or existing is None:
            return JSONResponse({"error": "request not found"}, status_code=404)

        system_prompt = (
            "You help a guardian write a short content-blocking rule. Reply with ONLY 1-2 plain "
            "sentences naming the CATEGORY of content to block for similar future requests. Do "
            "not name the specific site or URL. Start with 'Block'. Keep it under 200 characters."
        )
        if existing.kind == "search":
            user_prompt = (
                f"A child (age {owner.age}) searched: {existing.keyword!r}\n"
                f"Blocked reason: {existing.reason}\n"
                f"Child note: {existing.note or '(none)'}\n\n"
                "Describe the category of search topics to block going forward."
            )
        else:
            user_prompt = (
                f"Child age: {owner.age}\n"
                f"Site: {existing.host}\n"
                f"Blocked reason: {existing.reason}\n"
                f"Child note: {existing.note or '(none)'}\n\n"
                "Describe the category of websites to block going forward."
            )

        try:
            raw = await asyncio.wait_for(
                classifier.generate(system_prompt=system_prompt, user_prompt=user_prompt),
                timeout=config.classify_timeout_s,
            )
        except Exception:  # noqa: BLE001 - best-effort; the reject path never depends on this
            return JSONResponse({"error": "rule generation failed"}, status_code=502)
        rule = raw.strip()[:300]
        if not rule:
            return JSONResponse({"error": "empty rule"}, status_code=502)
        event_log.log("suggest_block_rule", id=request_id, profile=owner.name, kind=existing.kind)
        return JSONResponse({"rule": rule})

    async def review_activity_suggest_rule(request: Request) -> JSONResponse:
        # Draft a natural-language "block similar content" rule from one Activity item (a browsed
        # URL), feeding the Activity-page rule builder's "AI-suggested" kind. PIN-gated, READ-ONLY,
        # best-effort: mirrors review_suggest_block_rule but is seeded by {url} (an activity row)
        # rather than a stored access-request id, so it needs no request lookup.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        url = str(body.get("url", "")).strip()
        if not url:
            return JSONResponse({"error": "url required"}, status_code=422)

        # Age tunes the prompt; an unresolved/ambiguous profile just falls back to the default.
        rt = _resolve_parent_profile(str(body.get("profile", "")).strip())
        age = rt.age if rt is not None else DEFAULT_AGE
        host = extract_host(url) or url
        event = str(body.get("event", "")).strip()
        outcome = "was blocked" if event in ("block", "blocklist_block") else "was visited"

        system_prompt = (
            "You help a guardian write a short content-blocking rule. Reply with ONLY 1-2 plain "
            "sentences naming the CATEGORY of content to block for similar sites. Do not name the "
            "specific site or URL. Start with 'Block'. Keep it under 200 characters."
        )
        user_prompt = (
            f"Child age: {age}\n"
            f"Site: {host}\n"
            f"This page {outcome}; the guardian wants to block similar content going forward.\n\n"
            "Describe the category of websites to block."
        )
        try:
            raw = await asyncio.wait_for(
                classifier.generate(system_prompt=system_prompt, user_prompt=user_prompt),
                timeout=config.classify_timeout_s,
            )
        except Exception:  # noqa: BLE001 - best-effort; the builder degrades to manual entry
            return JSONResponse({"error": "rule generation failed"}, status_code=502)
        rule = raw.strip()[:300]
        if not rule:
            return JSONResponse({"error": "empty rule"}, status_code=502)
        event_log.log(
            "activity_suggest_rule", host=host, profile=(rt.name if rt is not None else "")
        )
        return JSONResponse({"rule": rule})

    def _summarize_existing_rules(profile: str | None, *, max_per: int = 40) -> str:
        # Compact digest of the block rules already in force (per-teen + Global), so the model
        # avoids re-suggesting what's covered. Scoped to one teen when a profile filter is set.
        snap = _pm.snapshot()
        teens = [snap[profile]] if profile and profile in snap else list(snap.values())
        lines: list[str] = []
        for rt in [*teens, _pm.global_runtime()]:
            values = list(rt.blocklist.current().values)
            if values:
                lines.append(f"{rt.name} blocklist: " + ", ".join(values[:max_per]))
            guidance = rt.prompt_store.current().strip()
            if guidance:
                lines.append(f"{rt.name} guidance: {guidance[:400]}")
        return "\n".join(lines) if lines else "(no rules yet)"

    async def review_activity_suggest_rules(request: Request) -> JSONResponse:
        # Bulk "what should I block next?" helper for the Activity page: summarize recent activity
        # plus the rules already in force, then ask the model for NEW block-rule suggestions. PIN-
        # gated and READ-ONLY (returns drafts; the guardian applies them via /review/blocklist).
        # The output is parsed fail-safe, so a confused model yields no suggestions, never a 500.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        profile = str(body.get("profile", "")).strip() or None
        raw_limit = body.get("limit")
        limit = _parse_activity_limit(None if raw_limit is None else str(raw_limit))

        loop = asyncio.get_running_loop()
        events = await loop.run_in_executor(
            None,
            functools.partial(event_log.recent, limit, profile=profile, events=ACTIVITY_EVENTS),
        )
        if not events:
            # No activity to reason about — skip the LLM call entirely.
            return JSONResponse({"suggestions": []})

        system_prompt = (
            "You help a guardian tighten web filtering for their kids. Given recent browsing "
            "activity and the rules already in force, propose UP TO "
            f"{_MAX_RULE_SUGGESTIONS} NEW blocking rules not already covered. Reply with ONLY a "
            'JSON array; each element an object {"kind","value","reason"} where kind is "exact" (a '
            'hostname), "wildcard" (a host/path containing *), or "nl" (a short natural-language '
            "topic). Keep value under 200 chars and reason to one sentence. If nothing new is "
            "worth blocking, reply with []."
        )
        user_prompt = (
            f"RECENT ACTIVITY:\n{_summarize_activity(events)}\n\n"
            f"RULES ALREADY IN FORCE:\n{_summarize_existing_rules(profile)}\n\n"
            "Propose new blocking rules as a JSON array."
        )
        try:
            raw = await asyncio.wait_for(
                classifier.generate(system_prompt=system_prompt, user_prompt=user_prompt),
                timeout=config.classify_timeout_s,
            )
        except Exception:  # noqa: BLE001 - best-effort; the page degrades to manual rule creation
            return JSONResponse({"error": "rule suggestion failed"}, status_code=502)
        suggestions = _parse_rule_suggestions(raw)
        event_log.log("activity_suggest_rules", profile=profile or "", count=len(suggestions))
        return JSONResponse({"suggestions": suggestions})

    async def review_activity_summary(request: Request) -> JSONResponse:
        # Dashboard panel. GET returns the latest saved per-profile summary plus a staleness
        # flag (no LLM); POST reviews recent activity, writes one timestamped run, and returns
        # it. PIN-gated; generation is fail-safe (malformed model output → empty, never 500).
        guard = _require_pin(request)
        if guard is not None:
            return guard
        loop = asyncio.get_running_loop()
        has_activity = bool(
            await loop.run_in_executor(
                None, functools.partial(event_log.recent, 1, events=ACTIVITY_EVENTS)
            )
        )
        if request.method == "GET":
            latest = await loop.run_in_executor(None, functools.partial(summary_log.recent, 1))
            if latest:
                record = latest[0]
                ts = str(record.get("ts") or "")
                profiles = record.get("profiles")
                return JSONResponse(
                    {
                        "generated_at": ts or None,
                        "stale": _summary_is_stale(ts),
                        "has_activity": has_activity,
                        "profiles": profiles if isinstance(profiles, list) else [],
                    }
                )
            # Nothing saved yet → "stale" (worth generating) only if there's activity to review.
            return JSONResponse(
                {
                    "generated_at": None,
                    "stale": has_activity,
                    "has_activity": has_activity,
                    "profiles": [],
                }
            )

        # POST: generate a fresh summary and persist one timestamped run.
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        raw_limit = body.get("limit") if isinstance(body, dict) else None
        limit = _parse_activity_limit(
            str(raw_limit) if raw_limit is not None else str(SUMMARY_LIMIT_DEFAULT)
        )
        events = await loop.run_in_executor(
            None, functools.partial(event_log.recent, limit, events=ACTIVITY_EVENTS)
        )
        if not events:
            # No activity to summarize — skip the LLM and don't record an empty run.
            return JSONResponse(
                {"generated_at": None, "stale": False, "has_activity": False, "profiles": []}
            )
        ages = {name: rt.age for name, rt in _pm.snapshot().items()}
        system_prompt = (
            "You are reviewing a child's recent web browsing for their parent or guardian. For "
            "EACH profile in the activity below, write a short, factual review. Reply with ONLY a "
            'JSON object {"profiles":[{"profile","summary","trends","attention"}]} where summary '
            "is 1-3 plain sentences and trends/attention are arrays of short phrases. Include a "
            'profile only if it has activity. Under "attention" call out: repeated attempts to '
            "reach blocked sites; risky or age-inappropriate content; new or unusual sites; "
            "browsing at late-night / unusual hours; and anything else a guardian should notice. "
            "Use empty arrays when nothing applies. Be concise and factual; never invent activity "
            "that isn't listed."
        )
        try:
            raw = await asyncio.wait_for(
                classifier.generate(
                    system_prompt=system_prompt, user_prompt=_activity_digest(events, ages)
                ),
                timeout=config.classify_timeout_s,
            )
        except Exception:  # noqa: BLE001 - best-effort; the dashboard keeps the prior summary
            return JSONResponse({"error": "summary generation failed"}, status_code=502)
        # The summary is per real child — never the shared Global profile (it's fed only teen
        # activity, but drop any Global entry the model emits anyway).
        profiles = [
            p
            for p in _parse_activity_summary(raw)["profiles"]
            if p["profile"].strip().lower() != GLOBAL_PROFILE_NAME
        ]
        generated_at = datetime.now(UTC).isoformat()
        await loop.run_in_executor(
            None,
            functools.partial(
                summary_log.log,
                "activity_summary",
                event_count=len(events),
                period_hours=SUMMARY_STALE_AFTER_S // 3600,
                profiles=profiles,
            ),
        )
        return JSONResponse(
            {
                "generated_at": generated_at,
                "stale": False,
                "has_activity": True,
                "profiles": profiles,
            }
        )

    async def review_activity_summaries(request: Request) -> JSONResponse:
        # Activity-page "Summaries" tab: the saved summary runs, newest first. PIN-gated, no LLM.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        limit = _parse_activity_limit(request.query_params.get("limit"))
        loop = asyncio.get_running_loop()
        runs = await loop.run_in_executor(None, functools.partial(summary_log.recent, limit))
        return JSONResponse({"summaries": runs})

    def _resolve_parent_profile(name: str) -> ProfileRuntime | None:
        """Pick the profile a parent list write targets.

        Parents authenticate with the PIN (not a teen token), so the request carries no
        profile. ``"global"`` targets the shared Global profile; an explicit teen name targets
        that teen; an empty name auto-resolves to the sole teen (the common case), else an
        ambiguous multi-teen write must name one.
        """
        if name == GLOBAL_PROFILE_NAME:
            return _pm.global_runtime()
        current = _pm.snapshot()
        if name:
            return current.get(name)
        if len(current) == 1:
            return next(iter(current.values()))
        return None

    async def _clear_caches_after_list_change(rt: ProfileRuntime) -> None:
        # A Global edit affects every teen, so clear all teen caches; a per-teen edit clears
        # just that teen's. (Hard URL rules are checked before the cache, so this matters
        # mainly for natural-language topic edits, which only the AI path consults.)
        loop = asyncio.get_running_loop()
        targets = list(_pm.snapshot().values()) if rt.name == GLOBAL_PROFILE_NAME else [rt]
        for target in targets:
            await loop.run_in_executor(None, target.cache.clear)

    async def _apply_block_rule(
        owner: ProfileRuntime,
        *,
        scope: str,
        rule: str | None,
        hard: bool,
        kind: str,
        existing: AccessRequest,
    ) -> tuple[bool, bool]:
        """On reject, optionally add a generalized block for *similar* content.

        Two complementary effects, both optional and scoped to this teen (``"profile"``) or the
        shared Global profile (``"global"``):
          - ``rule``: a natural-language line appended to the classifier prompt (semantic match).
          - ``hard``: the exact host (URL) or keyword (search) added to the hard list (AI-free).

        Strictly best-effort: the reject is already persisted, so a rejected/oversized/failed
        write is logged and reported as not-applied, never raised. Returns
        ``(rule_applied, hard_block_applied)``.
        """
        if not rule and not hard:
            return False, False
        target = _pm.global_runtime() if scope == "global" else owner
        loop = asyncio.get_running_loop()
        applied_rule = False
        applied_hard = False
        if rule:
            if not _valid_prompt_text(rule):
                event_log.log("block_rule_skipped", reason="invalid", profile=target.name)
            else:
                applied_rule = await loop.run_in_executor(
                    None,
                    functools.partial(
                        target.prompt_store.append,
                        rule,
                        separator="\n\n",
                        max_chars=_MAX_PROMPT_CHARS,
                    ),
                )
                event_log.log(
                    "block_rule_added" if applied_rule else "block_rule_skipped",
                    reason=None if applied_rule else "prompt_full",
                    profile=target.name,
                    scope=scope,
                    kind=kind,
                )
        if hard:
            entry = (existing.keyword or "") if kind == "search" else (existing.host or "")
            entry = entry.strip()
            store = target.search_block if kind == "search" else target.blocklist
            if entry and len(entry) <= 512 and entry.isprintable():
                try:
                    await loop.run_in_executor(None, store.add, entry)
                    applied_hard = True
                    event_log.log(
                        "hard_block_added", profile=target.name, scope=scope, kind=kind, entry=entry
                    )
                except OSError:
                    event_log.log("hard_block_skipped", reason="write_failed", profile=target.name)
            else:
                event_log.log("hard_block_skipped", reason="no_target", profile=target.name)
        if applied_rule or applied_hard:
            await _clear_caches_after_list_change(target)
        return applied_rule, applied_hard

    async def _review_list(request: Request, kind: str) -> JSONResponse:
        # Shared parent-facing allow/deny list management (kind = "whitelist" | "blocklist"),
        # gated by the PIN (the teen-token /whitelist is the extension's path). GET aggregates
        # across every teen + Global; a write targets one profile's store.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        runtimes = [*_pm.snapshot().values(), _pm.global_runtime()]
        if request.method == "GET":
            entries = [
                {"value": value, "type": classify_entry(value), "profile": rt.name}
                for rt in runtimes
                for value in (rt.blocklist if kind == "blocklist" else rt.whitelist)
                .current()
                .values
            ]
            return JSONResponse({"entries": entries})
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        entry = str(body.get("entry", "")).strip()
        # A single bounded line (entries are injected verbatim into the classifier prompt, so
        # reject newlines / control chars).
        if not entry or len(entry) > 512 or not entry.isprintable():
            return JSONResponse(
                {"error": "entry must be a non-empty, single-line string (max 512 chars)"},
                status_code=422,
            )
        rt = _resolve_parent_profile(str(body.get("profile", "")).strip())
        if rt is None:
            return JSONResponse({"error": "profile required"}, status_code=422)
        store = rt.blocklist if kind == "blocklist" else rt.whitelist
        loop = asyncio.get_running_loop()
        try:
            if request.method == "POST":
                await loop.run_in_executor(None, store.add, entry)
            else:  # DELETE
                await loop.run_in_executor(None, store.remove, entry)
            await _clear_caches_after_list_change(rt)
        except OSError:
            return JSONResponse({"error": f"{kind} write failed"}, status_code=500)
        event_log.log(f"parent_{kind}_{request.method.lower()}", entry=entry, profile=rt.name)
        if request.method == "POST":
            return JSONResponse({"value": entry, "type": classify_entry(entry)})
        return JSONResponse({"ok": True})

    async def review_whitelist(request: Request) -> JSONResponse:
        return await _review_list(request, "whitelist")

    async def review_blocklist(request: Request) -> JSONResponse:
        return await _review_list(request, "blocklist")

    async def _review_search_keywords(request: Request, kind: str) -> JSONResponse:
        # Parent-facing search-keyword list management (kind = "allow" | "block"), PIN-gated.
        # GET aggregates across every teen + Global; a write targets one profile's keyword store.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        runtimes = [*_pm.snapshot().values(), _pm.global_runtime()]
        if request.method == "GET":
            entries = [
                {"value": value, "profile": rt.name}
                for rt in runtimes
                for value in (rt.search_allow if kind == "allow" else rt.search_block)
                .current()
                .values
            ]
            return JSONResponse({"entries": entries})
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        entry = str(body.get("entry", "")).strip()
        if not entry or len(entry) > 512 or not entry.isprintable():
            return JSONResponse(
                {"error": "entry must be a non-empty, single-line string (max 512 chars)"},
                status_code=422,
            )
        rt = _resolve_parent_profile(str(body.get("profile", "")).strip())
        if rt is None:
            return JSONResponse({"error": "profile required"}, status_code=422)
        store = rt.search_allow if kind == "allow" else rt.search_block
        loop = asyncio.get_running_loop()
        try:
            if request.method == "POST":
                await loop.run_in_executor(None, store.add, entry)
            else:  # DELETE
                await loop.run_in_executor(None, store.remove, entry)
            await _clear_caches_after_list_change(rt)
        except OSError:
            return JSONResponse({"error": "search-keyword write failed"}, status_code=500)
        event_log.log(
            f"parent_search_{kind}_{request.method.lower()}", entry=entry, profile=rt.name
        )
        if request.method == "POST":
            return JSONResponse({"value": entry})
        return JSONResponse({"ok": True})

    async def review_search_allow(request: Request) -> JSONResponse:
        return await _review_search_keywords(request, "allow")

    async def review_search_block(request: Request) -> JSONResponse:
        return await _review_search_keywords(request, "block")

    async def review_activity(request: Request) -> JSONResponse:
        # Read-only parent view of recent per-URL verdicts (PIN-gated). No mutation; admin/dwell
        # events are filtered out so the timeline shows only what each kid saw and how it ended.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        profile = request.query_params.get("profile", "").strip() or None
        limit = _parse_activity_limit(request.query_params.get("limit"))
        loop = asyncio.get_running_loop()
        events = await loop.run_in_executor(
            None,
            functools.partial(event_log.recent, limit, profile=profile, events=ACTIVITY_EVENTS),
        )
        return JSONResponse({"events": events})

    async def review_prompt(request: Request) -> JSONResponse:
        # Parent-facing per-profile (or Global) classification-prompt view/edit, PIN-gated. GET
        # returns the stored prompt, the age-band default, the effective merged guidance, and (for
        # a teen) the age. POST saves the prompt + optional age, then clears the verdict cache(s)
        # so the change applies on the next classification.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        if request.method == "GET":
            rt = _resolve_parent_profile(request.query_params.get("profile", "").strip())
            if rt is None:
                return JSONResponse({"error": "profile required"}, status_code=422)
            is_global = rt.name == GLOBAL_PROFILE_NAME
            return JSONResponse(
                {
                    "profile": rt.name,
                    "is_global": is_global,
                    "age": None if is_global else rt.age,
                    "prompt": rt.prompt_store.current(),
                    "default": "" if is_global else default_profile_prompt(rt.age),
                    "merged": "" if is_global else _pm.merged_policy(rt),
                }
            )
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        rt = _resolve_parent_profile(str(body.get("profile", "")).strip())
        if rt is None:
            return JSONResponse({"error": "profile required"}, status_code=422)
        prompt = str(body.get("prompt", ""))
        if not _valid_prompt_text(prompt):
            return JSONResponse(
                {"error": f"prompt must be printable text up to {_MAX_PROMPT_CHARS} chars"},
                status_code=422,
            )
        # Validate the optional age fully before any write, so a bad age never leaves a
        # half-applied edit (prompt saved but age rejected).
        is_global = rt.name == GLOBAL_PROFILE_NAME
        new_age: int | None = None
        age_raw = body.get("age")
        if age_raw is not None:
            if is_global:
                return JSONResponse({"error": "the Global profile has no age"}, status_code=422)
            try:
                new_age = int(age_raw)
            except (TypeError, ValueError):
                return JSONResponse({"error": "age must be an integer"}, status_code=422)
            if not MIN_AGE <= new_age <= MAX_AGE:
                return JSONResponse(
                    {"error": f"age must be between {MIN_AGE} and {MAX_AGE}"}, status_code=422
                )
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, rt.prompt_store.set, prompt)
            if new_age is not None:
                rt = _pm.set_age(rt.name, new_age)
            await _clear_caches_after_list_change(rt)
        except OSError:
            return JSONResponse({"error": "prompt write failed"}, status_code=500)
        event_log.log(
            "parent_prompt_post",
            profile=rt.name,
            age=None if is_global else rt.age,
            length=len(prompt),
        )
        return JSONResponse({"ok": True, "age": None if is_global else rt.age})

    # --- Agent apply: parent-approved config writes (POST /agent/apply) ---
    # Each action the agent may PROPOSE has a dispatcher here that RE-VALIDATES its params with the
    # same rules as the corresponding typed endpoint and writes through the same store method — so a
    # hallucinated or tampered proposal can never bypass validation or reach a destructive op.
    def _resolve_apply_profile(profile_str: str, *, teen_only: bool = False) -> ProfileRuntime:
        """Resolve the profile an apply targets, raising ``_AgentApplyError`` on failure.

        ``teen_only`` (prize grants) requires a named child and never resolves Global; otherwise
        ``"global"`` / a teen name / the sole teen (empty) are accepted, mirroring the typed
        parent endpoints.
        """
        if teen_only:
            owner = _pm.snapshot().get(profile_str)
            if owner is None:
                raise _AgentApplyError(
                    404 if profile_str else 422,
                    "unknown profile" if profile_str else "a specific child profile is required",
                )
            return owner
        rt = _resolve_parent_profile(profile_str)
        if rt is None:
            raise _AgentApplyError(
                404 if profile_str else 422,
                "unknown profile" if profile_str else "profile required",
            )
        return rt

    def _make_list_apply(
        store_attr: str, verb: str, kind: str
    ) -> Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]:
        """Build an add/remove dispatcher for a per-profile entry list (whitelist/keywords/etc.)."""

        async def _dispatch(profile_str: str, params: dict[str, Any]) -> dict[str, Any]:
            entry = str(params.get("entry", "")).strip()
            if not entry or len(entry) > 512 or not entry.isprintable():
                raise _AgentApplyError(
                    422, "entry must be a non-empty, single-line string (max 512 chars)"
                )
            rt = _resolve_apply_profile(profile_str)
            store = getattr(rt, store_attr)
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(
                    None, store.add if verb == "add" else store.remove, entry
                )
                await _clear_caches_after_list_change(rt)
            except OSError as exc:
                raise _AgentApplyError(500, f"{kind} write failed") from exc
            event_log.log(f"agent_{kind}_{verb}", entry=entry, profile=rt.name)
            return {"profile": rt.name, "entry": entry}

        return _dispatch

    async def _apply_prize_grant(profile_str: str, params: dict[str, Any]) -> dict[str, Any]:
        points = _clamp_prize_points(params.get("points"))
        if points is None:
            raise _AgentApplyError(
                422, f"points must be a non-zero integer within +-{_MAX_PRIZE_POINTS}"
            )
        owner = _resolve_apply_profile(profile_str, teen_only=True)
        raw_reason = params.get("reason")
        reason = raw_reason.strip()[:200] if isinstance(raw_reason, str) else ""
        loop = asyncio.get_running_loop()
        try:
            balance = await loop.run_in_executor(None, owner.prize_point_store.add, points)
        except OSError as exc:
            raise _AgentApplyError(500, "prize write failed") from exc
        event_log.log(
            "prize_points_earned",
            profile=owner.name,
            delta=points,
            reason=reason,
            balance_after=balance,
        )
        metrics.record_prize_grant(owner.name, points, balance)
        return {"profile": owner.name, "points": points, "balance": balance}

    async def _apply_prompt_set(profile_str: str, params: dict[str, Any]) -> dict[str, Any]:
        prompt = str(params.get("prompt", ""))
        if not _valid_prompt_text(prompt):
            raise _AgentApplyError(
                422, f"prompt must be printable text up to {_MAX_PROMPT_CHARS} chars"
            )
        rt = _resolve_apply_profile(profile_str)
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, rt.prompt_store.set, prompt)
            await _clear_caches_after_list_change(rt)
        except OSError as exc:
            raise _AgentApplyError(500, "prompt write failed") from exc
        event_log.log("agent_prompt_set", profile=rt.name, length=len(prompt))
        return {"profile": rt.name, "length": len(prompt)}

    async def _apply_time_policy_set(profile_str: str, params: dict[str, Any]) -> dict[str, Any]:
        text = str(params.get("text", "")).strip()
        if not text or len(text) > _MAX_TIME_TEXT:
            raise _AgentApplyError(422, f"text required (max {_MAX_TIME_TEXT} chars)")
        rt = _resolve_apply_profile(profile_str)
        # Convert the parent's natural-language limits into the structured policy with the same
        # prompt the /time/policy/parse preview uses, then save it (validated + clamped on parse).
        try:
            raw = await asyncio.wait_for(
                classifier.generate(system_prompt=_TIME_POLICY_SYSTEM_PROMPT, user_prompt=text),
                timeout=config.classify_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 - NL→policy conversion failed
            raise _AgentApplyError(502, "time policy parse failed") from exc
        policy = parse_time_policy(raw, source_text=text, updated_ts=datetime.now(UTC).isoformat())
        loop = asyncio.get_running_loop()
        try:
            saved = await loop.run_in_executor(None, rt.time_policy.set, policy)
        except OSError as exc:
            raise _AgentApplyError(500, "policy write failed") from exc
        event_log.log("time_policy_set", profile=rt.name)
        return {"profile": rt.name, "policy": time_policy_to_json(saved)}

    _apply_registry: dict[str, Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]] = {
        "whitelist.add": _make_list_apply("whitelist", "add", "whitelist"),
        "whitelist.remove": _make_list_apply("whitelist", "remove", "whitelist"),
        "blocklist.add": _make_list_apply("blocklist", "add", "blocklist"),
        "blocklist.remove": _make_list_apply("blocklist", "remove", "blocklist"),
        "search_allow.add": _make_list_apply("search_allow", "add", "search_allow"),
        "search_allow.remove": _make_list_apply("search_allow", "remove", "search_allow"),
        "search_block.add": _make_list_apply("search_block", "add", "search_block"),
        "search_block.remove": _make_list_apply("search_block", "remove", "search_block"),
        "time_policy.set": _apply_time_policy_set,
        "prize.grant": _apply_prize_grant,
        "prompt.set": _apply_prompt_set,
    }
    if set(_apply_registry) != _AGENT_APPLY_ACTIONS:  # keep the write surface defined in one place
        raise RuntimeError("agent apply registry out of sync with _AGENT_APPLY_ACTIONS")

    async def agent_apply(request: Request) -> JSONResponse:
        """Apply ONE parent-approved config change the agent proposed (PIN-gated, one per call).

        The action must be in the allow-listed registry (destructive ops are absent by design);
        the matching dispatcher re-validates params server-side before any write.
        """
        guard = _require_pin(request)
        if guard is not None:
            return guard
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        action = str(body.get("action", "")).strip()
        dispatch = _apply_registry.get(action)
        if dispatch is None:
            return JSONResponse({"error": "unknown or unsupported action"}, status_code=422)
        params = body.get("params")
        if not isinstance(params, dict):
            return JSONResponse({"error": "params must be an object"}, status_code=422)
        # Normalize null / non-string to "" (→ resolves to the sole teen, or 422). Never str(None).
        profile_raw = body.get("profile")
        profile_str = profile_raw.strip() if isinstance(profile_raw, str) else ""
        try:
            result = await dispatch(profile_str, params)
        except _AgentApplyError as exc:
            return JSONResponse({"error": exc.message}, status_code=exc.status)
        event_log.log("agent_apply", action=action, profile=str(result.get("profile", "")))
        return JSONResponse({"ok": True, "action": action, "result": result})

    async def settings_change_pin(request: Request) -> JSONResponse:
        # Rotate the parent PIN. Re-authenticate with the *current* PIN from the body (not the
        # header) so an unlocked-but-unattended tab can't silently change the credential. We
        # verify the supplied current PIN ourselves, so this does not use _require_pin.
        if not pin_store.is_configured():
            return JSONResponse({"error": "parent PIN not configured"}, status_code=503)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        current = str(body.get("current_pin", "")).strip()
        new_pin = str(body.get("new_pin", "")).strip()
        if not pin_store.verify(current):
            return JSONResponse({"error": "current PIN is incorrect"}, status_code=403)
        error = validate_pin_format(new_pin)
        if error is not None:
            return JSONResponse({"error": error}, status_code=400)
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, pin_store.set_pin, new_pin)
        except OSError:
            return JSONResponse({"error": "could not save the PIN"}, status_code=500)
        event_log.log("parent_pin_changed")  # records the event, never the PIN value
        return JSONResponse({"ok": True})

    def _profile_config(token: str) -> dict[str, str]:
        """The extension's guardian-config.json contents for a profile's token."""
        return {"token": token, "endpoint": f"http://{config.host}:{config.port}"}

    async def profiles_endpoint(request: Request) -> JSONResponse:
        # Parent-only profile management. GET lists profiles (never their tokens); POST creates
        # one and returns its freshly generated token + a ready-to-paste extension config ONCE
        # (the UI shows it then forgets it -- it is never re-fetchable).
        guard = _require_pin(request)
        if guard is not None:
            return guard
        if request.method == "GET":
            return JSONResponse({"profiles": _pm.list_profiles()})
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        loop = asyncio.get_running_loop()
        try:
            runtime, token = await loop.run_in_executor(None, _pm.create, str(body.get("name", "")))
        except InvalidProfileNameError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except ProfileExistsError:
            return JSONResponse(
                {"error": "a profile with that name already exists"}, status_code=409
            )
        except (ConfigError, OSError):
            return JSONResponse({"error": "could not create profile data"}, status_code=500)
        event_log.log("profile_created", profile=runtime.name)  # never the token
        return JSONResponse(
            {"name": runtime.name, "token": token, "config": _profile_config(token)},
            status_code=201,
        )

    async def profile_rename(request: Request) -> JSONResponse:
        guard = _require_pin(request)
        if guard is not None:
            return guard
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        name = request.path_params["name"]
        new_name = str(body.get("new_name", ""))
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, functools.partial(_pm.rename, name, new_name))
        except InvalidProfileNameError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except ProfileNotFoundError:
            return JSONResponse({"error": "profile not found"}, status_code=404)
        except ProfileExistsError:
            return JSONResponse(
                {"error": "a profile with that name already exists"}, status_code=409
            )
        except (ConfigError, OSError):
            return JSONResponse({"error": "could not rename profile"}, status_code=500)
        event_log.log("profile_renamed", profile=new_name.strip())
        return JSONResponse({"ok": True})

    async def profile_regenerate_token(request: Request) -> JSONResponse:
        guard = _require_pin(request)
        if guard is not None:
            return guard
        name = request.path_params["name"]
        loop = asyncio.get_running_loop()
        try:
            token = await loop.run_in_executor(None, _pm.regenerate_token, name)
        except ProfileNotFoundError:
            return JSONResponse({"error": "profile not found"}, status_code=404)
        except (ConfigError, OSError):
            return JSONResponse({"error": "could not regenerate token"}, status_code=500)
        event_log.log("profile_token_regenerated", profile=name)  # never the token
        return JSONResponse({"token": token, "config": _profile_config(token)})

    async def profile_delete(request: Request) -> JSONResponse:
        guard = _require_pin(request)
        if guard is not None:
            return guard
        name = request.path_params["name"]
        purge = request.query_params.get("purge", "").lower() in ("1", "true", "yes")
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, functools.partial(_pm.delete, name, purge=purge)
            )
        except ProfileNotFoundError:
            return JSONResponse({"error": "profile not found"}, status_code=404)
        event_log.log("profile_deleted", profile=name, purged=purge)
        return JSONResponse({"ok": True})

    # --- self-hosted extension distribution (force-install via enterprise policy) ---
    # The kid browser's managed policy force-installs the parental-control extension from
    # these two routes. They are UNAUTHENTICATED on purpose: Chrome's extension updater
    # cannot present the X-Guardian-Token (same rationale as /static and /health). Both
    # serve fixed filenames from a configured dir — no path params, so no traversal risk —
    # and 404 until scripts/pack-extension.sh has produced the artifacts.
    def _ext_artifact(filename: str, media_type: str) -> Response:
        path = Path(config.ext_dist_dir) / filename
        if not path.is_file():
            return Response("extension not packed", status_code=404, media_type="text/plain")
        return FileResponse(path, media_type=media_type)

    async def ext_updates(_request: Request) -> Response:
        return _ext_artifact("updates.xml", "text/xml")

    async def ext_crx(_request: Request) -> Response:
        return _ext_artifact("aegis.crx", "application/x-chrome-extension")

    app = Starlette(
        routes=[
            Route("/", home_page, methods=["GET"]),
            Route("/health", health),
            Route("/version", version_endpoint, methods=["GET"]),
            # Flagship "Agent" assistant: PIN-gated conversational chat (read-only; proposes
            # config changes the parent applies) — the dashboard's default landing page.
            Route("/agent/chat", agent_chat, methods=["POST"]),
            # Apply a single parent-approved config change the agent proposed (re-validated).
            Route("/agent/apply", agent_apply, methods=["POST"]),
            Route("/classify", classify, methods=["POST"]),
            Route("/search-classify", search_classify, methods=["POST"]),
            Route("/dwell", dwell, methods=["POST"]),
            Route("/whitelist", whitelist_endpoint, methods=["GET", "POST", "DELETE"]),
            Route("/access-request", access_request_endpoint, methods=["GET", "POST"]),
            Route("/search-request", search_request_endpoint, methods=["GET", "POST"]),
            Route("/time/state", time_state_endpoint, methods=["GET"]),
            Route("/time-request", time_request_endpoint, methods=["GET", "POST"]),
            Route("/setup", setup_page, methods=["GET"]),
            Route("/setup/status", setup_status, methods=["GET"]),
            Route("/setup/pin", setup_pin, methods=["POST"]),
            Route("/review", review_page, methods=["GET"]),
            Route("/review/requests", review_requests, methods=["GET"]),
            Route("/review/decision", review_decision, methods=["POST"]),
            Route("/review/time-requests", review_time_requests, methods=["GET"]),
            Route("/review/time-decision", review_time_decision, methods=["POST"]),
            Route("/review/time/usage", review_time_usage, methods=["GET"]),
            # Prize points: teen-facing (token) balance + self-serve redeem; parent-facing (PIN)
            # grant, balances, and the event feed for the Activity "Prize points" tab.
            Route("/prize-points", prize_points_state, methods=["GET"]),
            Route("/prize-points/redeem", prize_points_redeem, methods=["POST"]),
            Route("/review/prize-points", review_prize_points, methods=["GET"]),
            Route("/review/prize-points/grant", review_prize_grant, methods=["POST"]),
            Route("/review/prize-points/events", review_prize_events, methods=["GET"]),
            Route("/time/policy", time_policy_endpoint, methods=["GET", "PUT"]),
            Route("/time/policy/parse", time_policy_parse, methods=["POST"]),
            Route(
                "/review/suggest-block-rule",
                review_suggest_block_rule,
                methods=["POST"],
            ),
            Route("/review/whitelist", review_whitelist, methods=["GET", "POST", "DELETE"]),
            Route("/review/blocklist", review_blocklist, methods=["GET", "POST", "DELETE"]),
            Route(
                "/review/search-keywords/allow",
                review_search_allow,
                methods=["GET", "POST", "DELETE"],
            ),
            Route(
                "/review/search-keywords/block",
                review_search_block,
                methods=["GET", "POST", "DELETE"],
            ),
            Route("/review/activity", review_activity, methods=["GET"]),
            Route(
                "/review/activity/suggest-rule",
                review_activity_suggest_rule,
                methods=["POST"],
            ),
            Route(
                "/review/activity/suggest-rules",
                review_activity_suggest_rules,
                methods=["POST"],
            ),
            Route(
                "/review/activity/summary",
                review_activity_summary,
                methods=["GET", "POST"],
            ),
            Route(
                "/review/activity/summaries",
                review_activity_summaries,
                methods=["GET"],
            ),
            Route("/review/prompt", review_prompt, methods=["GET", "POST"]),
            Route("/settings/pin", settings_change_pin, methods=["POST"]),
            Route("/profiles", profiles_endpoint, methods=["GET", "POST"]),
            # More-specific paths first so {name} doesn't swallow /rename and /token.
            Route("/profiles/{name}/rename", profile_rename, methods=["POST"]),
            Route("/profiles/{name}/token", profile_regenerate_token, methods=["POST"]),
            Route("/profiles/{name}", profile_delete, methods=["DELETE"]),
            # Self-hosted extension force-install endpoints (unauthenticated; see handlers).
            Route("/ext/updates.xml", ext_updates, methods=["GET"]),
            Route("/ext/aegis.crx", ext_crx, methods=["GET"]),
            # Shared design-system assets (tokens, component CSS, self-hosted fonts, brand
            # SVG) for the served pages. No auth — purely static, public styling like the
            # page shells themselves. The /static prefix never shadows the exact routes above.
            Mount(
                "/static",
                app=StaticFiles(directory=Path(__file__).parent / "static"),
                name="static",
            ),
        ]
    )
    # Seed the per-profile balance gauges so the 14-day prize-points line is continuous from
    # startup (not just from the first grant/redeem of this process).
    for _rt in _pm.snapshot().values():
        metrics.seed_prize_balance(_rt.name, _rt.prize_point_store.balance())
    app.state.config = config
    return app
