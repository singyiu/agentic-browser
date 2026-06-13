"""Starlette HTTP service exposing POST /classify and GET /health."""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import platform
import re
import socket
import subprocess
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .. import __version__ as _BACKEND_VERSION
from ..config import ConfigError
from .access_requests import RequestStore
from .blocklist import BlocklistStore
from .cache import VerdictCache
from .classifier import Classifier
from .config import DEFAULT_AGE, GuardianConfig
from .event_log import EventLog
from .keyword_store import KeywordStore
from .metrics import GuardianMetrics
from .normalize import extract_host
from .pin_store import PinStore
from .prize_points import (
    PrizePointStore,
)
from .profile_manager import (
    ProfileManager,
)
from .profiles import DEFAULT_PROFILE_NAME, ProfileRegistry
from .prompt import PromptStore
from .runtime import ProfileRuntime, build_runtime
from .time_ledger import TimeLedger
from .time_policy import (
    TimePolicyStore,
)
from .time_requests import TimeRequestStore
from .whitelist import WhitelistStore

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
# Cap a single dwell report: the heartbeat flushes every ~30s, so anything beyond hours
# is a forged or corrupt report that would otherwise burn the whole day's budget at once.
_MAX_DWELL_MS = 6 * 60 * 60 * 1000
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


def _is_loopback_client(request: Request) -> bool:
    """True when the TCP peer is this machine — the first-run setup trust anchor."""
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "::ffff:127.0.0.1")


class _SecurityHeadersMiddleware:
    """Add security headers as pure ASGI (no buffering — /dist/browser.zip streams 45MB).

    ``X-Content-Type-Options: nosniff`` on every response; ``frame-ancestors 'self'``
    only on HTML (clickjacking defense for the dashboard/setup pages — embedding
    Grafana via iframe SRC inside our pages is frame-src and stays unaffected).
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                if headers.get("content-type", "").startswith("text/html"):
                    headers["Content-Security-Policy"] = "frame-ancestors 'self'"
            await send(message)

        await self._app(scope, receive, send_with_headers)


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


# --- Setup & health console (GET /setup/health) -----------------------------------------------
# A friendly, NON-SECRET status payload for the setup wizard / devices console. It answers "is the
# guardian ready, and what's left to do?" without ever exposing the token value or the PIN. Every
# probe is best-effort and degrades to a safe default — this endpoint never raises a 500.
def _lan_ip() -> str | None:
    """This machine's primary LAN IPv4 (the source IP for outbound traffic), or None.

    A ``connect`` on a UDP socket only fixes the routing/source address and sends no packets, so
    this is fast and side-effect-free even when the TEST-NET target is unreachable.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 53))  # RFC 5737 TEST-NET-1 — never actually contacted
        return str(sock.getsockname()[0])
    except OSError:
        return None
    finally:
        sock.close()


def _firewall_state() -> str:
    """macOS application-firewall global state: 'on', 'off', or 'unknown' (best-effort)."""
    if platform.system() != "Darwin":
        return "unknown"
    tool = "/usr/libexec/ApplicationFirewall/socketfilterfw"
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell, bounded timeout
            [tool, "--getglobalstate"],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    out = result.stdout
    if "State = 0" in out:
        return "off"
    if "State = 1" in out or "State = 2" in out:
        return "on"
    return "unknown"


def _probe_network_firewall() -> tuple[str | None, str]:
    """Combine the two blocking probes (socket + subprocess) into one executor hop."""
    return _lan_ip(), _firewall_state()


def _extension_dist_status(dist_dir: str) -> dict[str, Any]:
    """Whether the kid extension CRX has been packed, plus its id/version (never raises)."""
    base = Path(dist_dir)
    ext_id: str | None = None
    try:
        id_file = base / "extension-id.txt"
        if id_file.is_file():
            raw = id_file.read_text(encoding="utf-8").strip()
            ext_id = raw if len(raw) == 32 else None
    except OSError:
        ext_id = None
    crx_present = (base / "aegis.crx").is_file()
    return {
        "packed": crx_present and ext_id is not None,
        "id": ext_id,
        "version": _read_extension_version(),
    }


# --- Per-kid enrollment (POST /enroll, GET /enroll/{profile}) ---------------------------------
# Packing a per-kid CRX is delegated to a packer callable so it can be faked in tests; the default
# shells out to scripts/pack-extension.sh with that kid's token + the guardian's LAN endpoint. The
# kid-setup .command served to each kid Mac is rendered from a template with those values baked in.
ExtPacker = Callable[[str, str, str], Awaitable[None]]  # (profile, token, endpoint) -> None

_KID_BOOTSTRAP_TEMPLATE = _REPO_ROOT / "agent-backend" / "deploy" / "kid-bootstrap.command.template"
_KID_UPDATE_CHECK_SCRIPT = _REPO_ROOT / "agent-backend" / "scripts" / "kid-update-check.sh"
_SAFE_PROFILE_SEG = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _render_kid_bootstrap(endpoint: str, profile: str) -> str:
    """Render the kid-setup .command for a profile (endpoint + profile substituted in).

    Also pins the SHA256 of the updater script: the bootstrap reaches the kid Mac
    out-of-band (AirDrop/USB), so a hash baked here lets it verify the later plain-HTTP
    download of kid-update-check.sh — the one fetch that becomes a login LaunchAgent.
    """
    text = _KID_BOOTSTRAP_TEMPLATE.read_text(encoding="utf-8")
    try:
        updater_sha = hashlib.sha256(_KID_UPDATE_CHECK_SCRIPT.read_bytes()).hexdigest()
    except OSError:
        updater_sha = ""  # the template skips verification when no pin was baked
    return (
        text.replace("__ENDPOINT__", endpoint)
        .replace("__PROFILE__", profile)
        .replace("__UPDATE_CHECK_SHA256__", updater_sha)
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
    ext_packer: ExtPacker | None = None,
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

    # --- per-kid enrollment ---
    # The default packer shells out to scripts/pack-extension.sh to build that kid's CRX (token +
    # LAN endpoint baked in) into {ext_dist_dir}/{profile}/. Tests inject a fake packer instead.
    async def _default_pack_profile_crx(profile: str, token: str, endpoint: str) -> None:
        out_dir = str(Path(config.ext_dist_dir) / profile)
        script = _REPO_ROOT / "agent-backend" / "scripts" / "pack-extension.sh"
        await asyncio.get_running_loop().run_in_executor(
            None,
            functools.partial(
                subprocess.run,
                [
                    "bash",
                    str(script),
                    "--profile",
                    profile,
                    "--token",
                    token,
                    "--endpoint",
                    endpoint,
                    "--out",
                    out_dir,
                ],
                check=True,
                capture_output=True,
                timeout=120,
            ),
        )

    # Route handlers live in the routes/ sub-package, each module exposing
    # build_routes(deps). Imported lazily here (not at module top) so the modules can
    # import shared module-level helpers back from this file without a circular import.
    from .routes import (
        agent as agent_routes,
    )
    from .routes import (
        classify as classify_routes,
    )
    from .routes import (
        dist as dist_routes,
    )
    from .routes import (
        prize as prize_routes,
    )
    from .routes import (
        profiles as profiles_routes,
    )
    from .routes import (
        requests as requests_routes,
    )
    from .routes import (
        review as review_routes,
    )
    from .routes import (
        review_activity as review_activity_routes,
    )
    from .routes import (
        setup as setup_routes,
    )
    from .routes import (
        time as time_routes,
    )
    from .routes.deps import GuardianDeps

    deps = GuardianDeps(
        config=config,
        classifier=classifier,
        pin_store=pin_store,
        event_log=event_log,
        summary_log=summary_log,
        metrics=metrics,
        pm=_pm,
        time_ledger=time_ledger,
        packer=ext_packer or _default_pack_profile_crx,
        repo_root=_REPO_ROOT,
    )
    app = Starlette(
        routes=[
            *setup_routes.build_routes(deps),
            *agent_routes.build_routes(deps),
            *classify_routes.build_routes(deps),
            *requests_routes.build_routes(deps),
            *time_routes.build_routes(deps),
            *prize_routes.build_routes(deps),
            *review_routes.build_routes(deps),
            *review_activity_routes.build_routes(deps),
            *profiles_routes.build_routes(deps),
            *dist_routes.build_routes(deps),
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
    app.add_middleware(_SecurityHeadersMiddleware)
    # Seed the per-profile balance gauges so the 14-day prize-points line is continuous from
    # startup (not just from the first grant/redeem of this process).
    for _rt in _pm.snapshot().values():
        metrics.seed_prize_balance(_rt.name, _rt.prize_point_store.balance())
    app.state.config = config
    return app
