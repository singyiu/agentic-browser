"""Screen-time accounting: how much active browsing a profile has used *today*.

Usage is **event-sourced**, not a stored counter: active-tab time arrives as ``dwell``
events and parent grants as ``time_grant`` events in the append-only event log. "Today"
is the window ``[local_midnight, next_local_midnight)`` in the configured household
timezone, computed at read time — so there is no midnight reset job (DST-safe) and the
client clock can never manufacture extra time (the server owns the clock).

For speed we keep a small in-memory accumulator per ``(profile, local_date)`` so the
hot path (heartbeat + every navigation) never rescans the log. A bucket is lazily
**seeded** from the log the first time it is touched, which recovers state after a
service restart. Buckets are mutated, never the policy; all returned views are immutable.

Caller contract: mutate the ledger *before* appending the matching event to the log
(``add_dwell`` then ``event_log.log("dwell", ...)``). The first touch seeds from the log
as it stands; counting the not-yet-logged current event separately avoids double-counting.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .event_log import EventLog
from .time_policy import (
    MS_PER_MINUTE,
    TimePolicy,
    in_bedtime,
    minutes_for,
    site_rule_for,
)
from .whitelist import canonicalize_url

# A generous cap on how many recent dwell/grant events to scan when seeding a day bucket.
# One profile generates one dwell event per page transition; 50k comfortably covers a day.
SEED_LIMIT = 50_000
_LEDGER_EVENTS = ("dwell", "time_grant")


@dataclass(frozen=True, slots=True)
class SiteUsage:
    """Per-site view for the host of the current tab (None when the host has no rule)."""

    host: str
    excluded: bool
    used_ms: int
    limit_ms: int | None  # None = no own cap
    remaining_ms: int | None
    blocked: bool


@dataclass(frozen=True, slots=True)
class Usage:
    """Immutable snapshot of a profile's current time state for a given host."""

    general_used_ms: int
    general_limit_ms: int | None  # None = no general limit configured
    general_remaining_ms: int | None
    blocked_general: bool
    bedtime_active: bool
    blocked: bool  # whether the *current host* should be blocked right now
    site: SiteUsage | None


@dataclass(slots=True)
class _Bucket:
    by_host_ms: dict[str, int] = field(default_factory=dict)
    granted_minutes: int = 0
    seeded: bool = False


def _resolve_tz(tz: str) -> ZoneInfo | object:
    if tz:
        try:
            return ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    # Fall back to the server's local timezone (fixed current offset).
    return datetime.now(UTC).astimezone().tzinfo or UTC


def _aware(now: datetime) -> datetime:
    return now if now.tzinfo is not None else now.replace(tzinfo=UTC)


def _host_matches(rule_host: str, stored_host: str) -> bool:
    canon = canonicalize_url(stored_host).split("/", 1)[0]
    return canon == rule_host or canon.endswith("." + rule_host)


class TimeLedger:
    """In-memory daily accumulators over the event log, bucketed by household-local date."""

    def __init__(self, event_log: EventLog, *, tz: str = "") -> None:
        self._event_log = event_log
        self._tz = _resolve_tz(tz)
        self._lock = threading.Lock()
        self._buckets: dict[tuple[str, str], _Bucket] = {}

    # --- time helpers ---

    def _local(self, now: datetime) -> datetime:
        return _aware(now).astimezone(self._tz)

    def _date_key(self, now: datetime) -> str:
        return self._local(now).date().isoformat()

    def _day_bounds_utc(self, now: datetime) -> tuple[datetime, datetime]:
        local = self._local(now)
        start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(days=1)
        return start_local.astimezone(UTC), end_local.astimezone(UTC)

    def day_bounds_utc(self, now: datetime) -> tuple[datetime, datetime]:
        """UTC ``[start, end)`` of the household-local day containing ``now``.

        Public wrapper so other accounting (e.g. the prize-point daily cap) shares the
        exact same notion of "today" as screen-time, including the configured timezone.
        """
        return self._day_bounds_utc(now)

    # --- bucket management ---

    def _bucket(self, profile: str, now: datetime) -> _Bucket:
        """Return today's bucket for ``profile``, seeding it from the log on first touch.

        Must be called with ``self._lock`` held.
        """
        key = (profile, self._date_key(now))
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket()
            self._buckets[key] = bucket
        if not bucket.seeded:
            self._seed(bucket, profile, now)
            bucket.seeded = True
        return bucket

    def _seed(self, bucket: _Bucket, profile: str, now: datetime) -> None:
        start, end = self._day_bounds_utc(now)
        for record in self._event_log.recent(SEED_LIMIT, profile=profile, events=_LEDGER_EVENTS):
            ts = _parse_ts(record.get("ts"))
            if ts is None or not (start <= ts < end):
                continue
            if record.get("event") == "dwell":
                host = record.get("host")
                ms = record.get("dwell_ms")
                if (
                    isinstance(host, str)
                    and isinstance(ms, (int, float))
                    and not isinstance(ms, bool)
                ):
                    bucket.by_host_ms[host] = bucket.by_host_ms.get(host, 0) + int(ms)
            elif record.get("event") == "time_grant":
                minutes = record.get("minutes")
                if isinstance(minutes, (int, float)) and not isinstance(minutes, bool):
                    bucket.granted_minutes += int(minutes)

    # --- mutations ---

    def add_dwell(self, profile: str, host: str, dwell_ms: int, now: datetime) -> None:
        if not host or dwell_ms <= 0:
            return
        with self._lock:
            bucket = self._bucket(profile, now)
            bucket.by_host_ms[host] = bucket.by_host_ms.get(host, 0) + int(dwell_ms)

    def add_grant(self, profile: str, minutes: int, now: datetime) -> None:
        if minutes <= 0:
            return
        with self._lock:
            bucket = self._bucket(profile, now)
            bucket.granted_minutes += int(minutes)

    # --- read ---

    def usage(self, profile: str, policy: TimePolicy, url_host: str | None, now: datetime) -> Usage:
        with self._lock:
            bucket = self._bucket(profile, now)
            by_host = dict(bucket.by_host_ms)
            granted = bucket.granted_minutes

        local = self._local(now)
        # General pool: sum time on hosts that are NOT excluded by a site rule.
        general_used = sum(ms for host, ms in by_host.items() if not _is_excluded(policy, host))
        limit_min = minutes_for(policy, local.weekday())
        if limit_min is None:
            general_limit_ms: int | None = None
            general_remaining: int | None = None
            blocked_general = False
        else:
            general_limit_ms = (limit_min + granted) * MS_PER_MINUTE
            general_remaining = max(0, general_limit_ms - general_used)
            blocked_general = general_used >= general_limit_ms

        bedtime = in_bedtime(policy, local)

        site: SiteUsage | None = None
        rule = site_rule_for(policy, url_host) if url_host else None
        if rule is not None:
            site_used = sum(ms for host, ms in by_host.items() if _host_matches(rule.host, host))
            if rule.daily_minutes is None:
                site_limit_ms: int | None = None
                site_remaining: int | None = None
                site_blocked = False
            else:
                site_limit_ms = rule.daily_minutes * MS_PER_MINUTE
                site_remaining = max(0, site_limit_ms - site_used)
                site_blocked = site_used >= site_limit_ms
            site = SiteUsage(
                host=rule.host,
                excluded=rule.excluded,
                used_ms=site_used,
                limit_ms=site_limit_ms,
                remaining_ms=site_remaining,
                blocked=site_blocked,
            )

        if rule is not None and rule.excluded:
            # Excluded hosts are exempt from the general pool AND bedtime; only an own cap blocks.
            blocked = bool(site and site.blocked)
        else:
            blocked = bedtime or blocked_general or bool(site and site.blocked)

        return Usage(
            general_used_ms=general_used,
            general_limit_ms=general_limit_ms,
            general_remaining_ms=general_remaining,
            blocked_general=blocked_general,
            bedtime_active=bedtime,
            blocked=blocked,
            site=site,
        )


def _is_excluded(policy: TimePolicy, host: str) -> bool:
    rule = site_rule_for(policy, host)
    return bool(rule and rule.excluded)


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
