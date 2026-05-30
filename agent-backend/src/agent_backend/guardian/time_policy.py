"""Per-profile screen-time policy: a daily minute budget (by weekday), bedtime
windows, and per-site overrides.

The parent describes limits in natural language; the classifier turns that into a JSON
object which :func:`parse_policy` validates and clamps into a :class:`TimePolicy`. The
policy is stored as JSON per profile and layered with the Global profile exactly like
classification rules: :func:`resolve` lets a teen's policy fall back to Global when a
field is unset, and merges per-site rules with the teen winning on a host conflict.

Everything here is fail-safe: malformed input never raises, it degrades to :data:`EMPTY`
(an unset policy, i.e. "no limit"). Records are immutable; edits rebuild a new policy.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .whitelist import canonicalize_url

WEEKDAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_KEYS: tuple[str, ...] = ("default", *WEEKDAYS)
MAX_DAILY_MINUTES = 1440  # a full day; the parent cannot grant more than 24h/day
MAX_SITES = 50
MAX_WINDOWS = 14
MS_PER_MINUTE = 60_000

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")  # 24h "H:MM" / "HH:MM"
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)
_FULL_DAY = {
    "monday": "mon",
    "tuesday": "tue",
    "wednesday": "wed",
    "thursday": "thu",
    "friday": "fri",
    "saturday": "sat",
    "sunday": "sun",
}


@dataclass(frozen=True, slots=True)
class BedtimeWindow:
    """A scheduled hard-block window. ``days`` empty = every day. ``end`` <= ``start``
    wraps past midnight (e.g. 21:00 -> 07:00)."""

    days: tuple[str, ...]
    start: str  # "HH:MM"
    end: str  # "HH:MM"


@dataclass(frozen=True, slots=True)
class SiteRule:
    """Per-site override. ``excluded`` exempts the host from the general pool (and the
    general/bedtime block); ``daily_minutes`` is an optional own cap (None = uncapped)."""

    host: str  # canonicalized (no scheme / leading www / trailing slash)
    daily_minutes: int | None
    excluded: bool


@dataclass(frozen=True, slots=True)
class TimePolicy:
    """A profile's screen-time configuration (immutable)."""

    daily_minutes: dict[str, int]  # keys from DAY_KEYS; "default" applies when a day absent
    windows: tuple[BedtimeWindow, ...]
    sites: tuple[SiteRule, ...]
    source_text: str  # the parent's natural-language description (round-tripped for editing)
    updated_ts: str

    def is_set(self) -> bool:
        """True when this policy carries any actual configuration (drives Global fallback)."""
        return bool(self.daily_minutes) or bool(self.windows) or bool(self.sites)


EMPTY = TimePolicy(daily_minutes={}, windows=(), sites=(), source_text="", updated_ts="")


# --- validation / clamping helpers (lenient; never raise) ---


def _clamp_day_minutes(value: object) -> int | None:
    """A required daily-minute count: int in [0, 1440], else ``None`` (drop the entry)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = int(value)
    elif isinstance(value, str):
        try:
            n = int(value.strip())
        except ValueError:
            return None
    else:
        return None
    return max(0, min(MAX_DAILY_MINUTES, n))


def _clamp_site_minutes(value: object) -> int | None:
    """An optional per-site cap. Missing/None/invalid -> ``None`` (no own cap)."""
    if value is None:
        return None
    return _clamp_day_minutes(value)


def _expand_day_key(key: object) -> tuple[str, ...]:
    """Map a (possibly natural-language) day key to canonical DAY_KEYS entries."""
    if not isinstance(key, str):
        return ()
    k = key.strip().lower()
    if k in DAY_KEYS:
        return (k,)
    if k in _FULL_DAY:
        return (_FULL_DAY[k],)
    if k[:3] in WEEKDAYS:
        return (k[:3],)
    if k in ("weekday", "weekdays", "schoolday", "schooldays", "school night", "school nights"):
        return ("mon", "tue", "wed", "thu", "fri")
    if k in ("weekend", "weekends"):
        return ("sat", "sun")
    if k in ("all", "any", "daily", "everyday", "every day", "default"):
        return ("default",)
    return ()


def _coerce_day_minutes(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in raw.items():
        minutes = _clamp_day_minutes(value)
        if minutes is None:
            continue
        for canon in _expand_day_key(key):
            out[canon] = minutes
    return out


def _norm_time(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip()
    m = _TIME_RE.match(v)
    if not m:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"  # zero-pad the hour


def _coerce_days(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    days: list[str] = []
    for item in raw:
        for canon in _expand_day_key(item):
            if canon in WEEKDAYS and canon not in days:
                days.append(canon)
    return tuple(days)


def _coerce_window(raw: object) -> BedtimeWindow | None:
    if not isinstance(raw, dict):
        return None
    start = _norm_time(raw.get("start"))
    end = _norm_time(raw.get("end"))
    if start is None or end is None or start == end:
        return None  # need two distinct, valid times to define a window
    return BedtimeWindow(days=_coerce_days(raw.get("days")), start=start, end=end)


def _looks_like_host(canon: str) -> bool:
    return bool(canon) and "." in canon and not any(ch.isspace() for ch in canon)


def _coerce_site(raw: object) -> SiteRule | None:
    if not isinstance(raw, dict):
        return None
    host_raw = raw.get("host") or raw.get("site") or raw.get("domain") or raw.get("url")
    if not isinstance(host_raw, str):
        return None
    host = canonicalize_url(host_raw).split("/", 1)[0]
    if not _looks_like_host(host):
        return None
    minutes = _clamp_site_minutes(raw.get("daily_minutes", raw.get("minutes")))
    return SiteRule(host=host, daily_minutes=minutes, excluded=bool(raw.get("excluded")))


def _coerce_policy(obj: object, *, source_text: str = "", updated_ts: str = "") -> TimePolicy:
    """Validate a dict into a :class:`TimePolicy`; any failure degrades to :data:`EMPTY`."""
    if not isinstance(obj, dict):
        return EMPTY
    daily = _coerce_day_minutes(obj.get("daily_minutes"))
    windows = tuple(
        w for w in (_coerce_window(x) for x in _as_list(obj.get("windows"))) if w is not None
    )[:MAX_WINDOWS]
    sites = tuple(
        s for s in (_coerce_site(x) for x in _as_list(obj.get("sites"))) if s is not None
    )[:MAX_SITES]
    # de-dupe sites by host (last wins), preserving order
    if sites:
        sites = tuple({s.host: s for s in sites}.values())
    text = source_text or (
        obj.get("source_text") if isinstance(obj.get("source_text"), str) else ""
    )
    ts = updated_ts or (obj.get("updated_ts") if isinstance(obj.get("updated_ts"), str) else "")
    return TimePolicy(
        daily_minutes=daily,
        windows=windows,
        sites=sites,
        source_text=text or "",
        updated_ts=ts or "",
    )


def _as_list(value: object) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


# --- public parse / serialize / resolve ---


def parse_policy(raw: str, *, source_text: str = "", updated_ts: str = "") -> TimePolicy:
    """Parse the classifier's reply into a policy. Tries the whole string as JSON, then a
    ``{...}`` slice; any failure returns :data:`EMPTY`. Never raises."""
    candidates: list[str] = []
    if isinstance(raw, str) and raw.strip():
        candidates.append(raw)
        match = _JSON_OBJ_RE.search(raw)
        if match:
            candidates.append(match.group(0))
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        return _coerce_policy(obj, source_text=source_text, updated_ts=updated_ts)
    return _coerce_policy(None, source_text=source_text, updated_ts=updated_ts)


def from_stored(obj: object) -> TimePolicy:
    """Build a policy from its on-disk dict (carries source_text/updated_ts)."""
    return _coerce_policy(obj)


def to_json(policy: TimePolicy) -> dict[str, Any]:
    """Serialize a policy to a JSON-able dict (storage + API responses)."""
    return {
        "daily_minutes": dict(policy.daily_minutes),
        "windows": [{"days": list(w.days), "start": w.start, "end": w.end} for w in policy.windows],
        "sites": [
            {"host": s.host, "daily_minutes": s.daily_minutes, "excluded": s.excluded}
            for s in policy.sites
        ],
        "source_text": policy.source_text,
        "updated_ts": policy.updated_ts,
    }


def resolve(teen: TimePolicy, glob: TimePolicy) -> TimePolicy:
    """Effective policy = teen's fields when set, else Global's; sites merged by host
    (teen overrides). Mirrors how classification rules layer Global under each teen."""
    daily = teen.daily_minutes if teen.daily_minutes else glob.daily_minutes
    windows = teen.windows if teen.windows else glob.windows
    by_host: dict[str, SiteRule] = {s.host: s for s in glob.sites}
    for site in teen.sites:
        by_host[site.host] = site
    text = teen.source_text if teen.is_set() else glob.source_text
    ts = teen.updated_ts if teen.is_set() else glob.updated_ts
    return replace(
        EMPTY,
        daily_minutes=dict(daily),
        windows=tuple(windows),
        sites=tuple(by_host.values()),
        source_text=text,
        updated_ts=ts,
    )


# --- query helpers ---


def minutes_for(policy: TimePolicy, weekday: int) -> int | None:
    """The general daily budget for a weekday (0=Mon..6=Sun). ``None`` = no limit."""
    if not 0 <= weekday <= 6:
        return policy.daily_minutes.get("default")
    day = WEEKDAYS[weekday]
    if day in policy.daily_minutes:
        return policy.daily_minutes[day]
    return policy.daily_minutes.get("default")


def _minutes_of_day(now_local: datetime) -> int:
    return now_local.hour * 60 + now_local.minute


def _window_active(window: BedtimeWindow, now_local: datetime) -> bool:
    if window.days and WEEKDAYS[now_local.weekday()] not in window.days:
        return False
    start = int(window.start[:2]) * 60 + int(window.start[3:])
    end = int(window.end[:2]) * 60 + int(window.end[3:])
    now = _minutes_of_day(now_local)
    if start < end:
        return start <= now < end
    return now >= start or now < end  # wraps past midnight


def in_bedtime(policy: TimePolicy, now_local: datetime) -> bool:
    """True when ``now_local`` falls inside any bedtime window."""
    return any(_window_active(w, now_local) for w in policy.windows)


def site_rule_for(policy: TimePolicy, host: str) -> SiteRule | None:
    """The site rule matching ``host`` (exact canonical host or a subdomain of it)."""
    canon = canonicalize_url(host).split("/", 1)[0]
    if not canon:
        return None
    for rule in policy.sites:
        if canon == rule.host or canon.endswith("." + rule.host):
            return rule
    return None


# --- store ---


def _coerce_stored(data: object) -> TimePolicy:
    return from_stored(data)


class TimePolicyStore:
    """Owns one profile's ``time_policy.json``; hot-reloads on mtime change.

    A missing or malformed file yields :data:`EMPTY` (fails safe: no limit, never a
    spurious block). Mirrors :class:`whitelist.WhitelistStore`.
    """

    def __init__(self, path: str) -> None:
        self._path = Path(path).expanduser()
        self._lock = threading.Lock()
        self._mtime = self._stat_mtime()
        self._current = self._read()

    def _stat_mtime(self) -> float | None:
        try:
            return self._path.stat().st_mtime
        except OSError:
            return None

    def _read(self) -> TimePolicy:
        try:
            data = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return EMPTY
        return _coerce_stored(data)

    def _write(self, policy: TimePolicy) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(to_json(policy), indent=2))

    def current(self) -> TimePolicy:
        with self._lock:
            mtime = self._stat_mtime()
            if mtime != self._mtime:
                self._mtime = mtime
                self._current = self._read()
            return self._current

    def set(self, policy: TimePolicy) -> TimePolicy:
        with self._lock:
            self._write(policy)
            self._mtime = self._stat_mtime()
            self._current = policy
            return policy
