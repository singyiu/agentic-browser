"""Unit tests for the event-sourced screen-time ledger."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agent_backend.guardian.event_log import EventLog
from agent_backend.guardian.time_ledger import TimeLedger
from agent_backend.guardian.time_policy import parse_policy

NOW = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)  # a Saturday, noon UTC


def _policy(obj: dict) -> object:
    return parse_policy(json.dumps(obj))


def _ledger(tmp_path: Path) -> tuple[TimeLedger, EventLog]:
    log = EventLog(str(tmp_path / "events.jsonl"))
    return TimeLedger(log, tz="UTC"), log


def _seed_events(tmp_path: Path, records: list[dict]) -> EventLog:
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return EventLog(str(path))


# --- no policy -> unlimited ---


def test_no_policy_is_unlimited(tmp_path: Path) -> None:
    ledger, _ = _ledger(tmp_path)
    ledger.add_dwell("kid", "a.com", 5_000_000, NOW)
    u = ledger.usage("kid", _policy({}), "a.com", NOW)
    assert u.general_limit_ms is None
    assert u.blocked is False and u.blocked_general is False


# --- accumulation + general block ---


def test_add_dwell_accumulates_then_blocks(tmp_path: Path) -> None:
    ledger, _ = _ledger(tmp_path)
    policy = _policy({"daily_minutes": {"default": 1}})  # 60_000 ms
    ledger.add_dwell("kid", "a.com", 30_000, NOW)
    u = ledger.usage("kid", policy, "a.com", NOW)
    assert u.general_used_ms == 30_000
    assert u.general_remaining_ms == 30_000
    assert u.blocked is False

    ledger.add_dwell("kid", "a.com", 40_000, NOW)  # total 70_000 > 60_000
    u = ledger.usage("kid", policy, "a.com", NOW)
    assert u.blocked_general is True
    assert u.general_remaining_ms == 0
    assert u.blocked is True


# --- excluded site: not counted, exempt from the general block ---


def test_excluded_site_not_counted_and_exempt(tmp_path: Path) -> None:
    ledger, _ = _ledger(tmp_path)
    policy = _policy(
        {"daily_minutes": {"default": 1}, "sites": [{"host": "khanacademy.org", "excluded": True}]}
    )
    ledger.add_dwell("kid", "khanacademy.org", 600_000, NOW)  # 10 min on homework
    ledger.add_dwell("kid", "a.com", 70_000, NOW)  # blows the 1-min general budget

    general_host = ledger.usage("kid", policy, "a.com", NOW)
    assert general_host.general_used_ms == 70_000  # khan time excluded from the pool
    assert general_host.blocked is True

    khan = ledger.usage("kid", policy, "www.khanacademy.org", NOW)
    assert khan.site is not None and khan.site.excluded is True
    assert khan.blocked is False  # stays usable after the general budget is gone


# --- per-site cap ---


def test_per_site_cap_blocks_only_that_site(tmp_path: Path) -> None:
    ledger, _ = _ledger(tmp_path)
    policy = _policy({"sites": [{"host": "g.com", "daily_minutes": 1}]})  # no general limit
    ledger.add_dwell("kid", "g.com", 70_000, NOW)
    capped = ledger.usage("kid", policy, "g.com", NOW)
    assert capped.site is not None and capped.site.blocked is True
    assert capped.blocked is True
    other = ledger.usage("kid", policy, "other.com", NOW)
    assert other.blocked is False  # general unlimited, no site rule


# --- grants raise the limit ---


def test_grant_raises_general_limit(tmp_path: Path) -> None:
    ledger, _ = _ledger(tmp_path)
    policy = _policy({"daily_minutes": {"default": 1}})
    ledger.add_dwell("kid", "a.com", 70_000, NOW)
    assert ledger.usage("kid", policy, "a.com", NOW).blocked is True
    ledger.add_grant("kid", 5, NOW)  # +5 minutes today
    u = ledger.usage("kid", policy, "a.com", NOW)
    assert u.general_limit_ms == 6 * 60_000
    assert u.blocked is False
    assert u.general_remaining_ms == 6 * 60_000 - 70_000


# --- bedtime hard-blocks regardless of remaining ---


def test_bedtime_blocks_even_under_budget(tmp_path: Path) -> None:
    ledger, _ = _ledger(tmp_path)
    policy = _policy(
        {"daily_minutes": {"default": 1000}, "windows": [{"start": "11:00", "end": "13:00"}]}
    )
    ledger.add_dwell("kid", "a.com", 1000, NOW)  # well under budget
    u = ledger.usage("kid", policy, "a.com", NOW)
    assert u.bedtime_active is True
    assert u.blocked is True
    assert u.blocked_general is False


# --- seeding from the log (restart recovery) ---


def test_seed_from_log_sums_today_only(tmp_path: Path) -> None:
    log = _seed_events(
        tmp_path,
        [
            {
                "ts": "2026-05-30T08:00:00+00:00",
                "event": "dwell",
                "host": "a.com",
                "dwell_ms": 20_000,
                "profile": "kid",
            },
            {
                "ts": "2026-05-30T09:00:00+00:00",
                "event": "dwell",
                "host": "a.com",
                "dwell_ms": 25_000,
                "profile": "kid",
            },
            {
                "ts": "2026-05-29T23:00:00+00:00",
                "event": "dwell",
                "host": "a.com",
                "dwell_ms": 99_000,
                "profile": "kid",
            },  # yesterday
            {
                "ts": "2026-05-30T10:00:00+00:00",
                "event": "time_grant",
                "minutes": 2,
                "profile": "kid",
            },
            {
                "ts": "2026-05-30T10:00:00+00:00",
                "event": "dwell",
                "host": "a.com",
                "dwell_ms": 5_000,
                "profile": "other",
            },  # other profile
        ],
    )
    ledger = TimeLedger(log, tz="UTC")
    u = ledger.usage("kid", _policy({"daily_minutes": {"default": 100}}), "a.com", NOW)
    assert u.general_used_ms == 45_000  # only today's kid dwell
    assert u.general_limit_ms == (100 + 2) * 60_000  # includes the grant


def test_seed_excludes_events_before_local_midnight(tmp_path: Path) -> None:
    log = _seed_events(
        tmp_path,
        [
            {
                "ts": "2026-05-30T23:59:00+00:00",
                "event": "dwell",
                "host": "a.com",
                "dwell_ms": 10_000,
                "profile": "kid",
            },
        ],
    )
    ledger = TimeLedger(log, tz="UTC")
    after_midnight = datetime(2026, 5, 31, 0, 30, tzinfo=UTC)
    u = ledger.usage("kid", _policy({"daily_minutes": {"default": 100}}), "a.com", after_midnight)
    assert u.general_used_ms == 0  # yesterday's event is outside the new day window


def test_seed_happens_once(tmp_path: Path) -> None:
    log = _seed_events(
        tmp_path,
        [
            {
                "ts": "2026-05-30T08:00:00+00:00",
                "event": "dwell",
                "host": "a.com",
                "dwell_ms": 20_000,
                "profile": "kid",
            },
        ],
    )
    ledger = TimeLedger(log, tz="UTC")
    policy = _policy({"daily_minutes": {"default": 100}})
    ledger.usage("kid", policy, "a.com", NOW)  # seeds: 20_000
    ledger.add_dwell("kid", "a.com", 5_000, NOW)  # +5_000 live
    u = ledger.usage("kid", policy, "a.com", NOW)
    assert u.general_used_ms == 25_000  # not re-seeded (would be 40_000 if double-counted)
