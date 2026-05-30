"""Unit tests for the screen-time policy model, parser, resolver, and store."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from agent_backend.guardian.time_policy import (
    EMPTY,
    BedtimeWindow,
    SiteRule,
    TimePolicy,
    TimePolicyStore,
    from_stored,
    in_bedtime,
    minutes_for,
    parse_policy,
    resolve,
    site_rule_for,
    to_json,
)

# --- parse_policy: happy path ---


def test_parse_full_object() -> None:
    raw = json.dumps(
        {
            "daily_minutes": {"default": 120, "fri": 180, "sat": 240, "sun": 240},
            "windows": [{"days": ["mon", "tue"], "start": "21:00", "end": "07:00"}],
            "sites": [{"host": "https://www.khanacademy.org/", "excluded": True}],
        }
    )
    p = parse_policy(raw)
    assert p.daily_minutes == {"default": 120, "fri": 180, "sat": 240, "sun": 240}
    assert p.windows == (BedtimeWindow(days=("mon", "tue"), start="21:00", end="07:00"),)
    assert p.sites == (SiteRule(host="khanacademy.org", daily_minutes=None, excluded=True),)
    assert p.is_set()


def test_parse_extracts_object_from_prose() -> None:
    raw = 'Sure! Here is the config:\n{"daily_minutes": {"default": 60}}\nHope that helps.'
    assert parse_policy(raw).daily_minutes == {"default": 60}


def test_parse_garbage_is_empty() -> None:
    assert parse_policy("not json at all") == EMPTY
    assert parse_policy("") == EMPTY
    assert parse_policy("[1,2,3]") == EMPTY  # array, not an object


def test_parse_clamps_minutes() -> None:
    p = parse_policy(json.dumps({"daily_minutes": {"default": 99999, "mon": -5}}))
    assert p.daily_minutes == {"default": 1440, "mon": 0}


def test_parse_expands_natural_language_day_keys() -> None:
    p = parse_policy(json.dumps({"daily_minutes": {"weekdays": 90, "weekend": 180, "monday": 30}}))
    assert p.daily_minutes == {
        "mon": 30,  # explicit monday overrides the weekdays expansion (later key wins)
        "tue": 90,
        "wed": 90,
        "thu": 90,
        "fri": 90,
        "sat": 180,
        "sun": 180,
    }


def test_parse_drops_invalid_windows_and_sites() -> None:
    raw = json.dumps(
        {
            "windows": [
                {"start": "21:00", "end": "07:00"},
                {"start": "9am", "end": "10am"},  # invalid time format
                {"start": "08:00", "end": "08:00"},  # zero-length
            ],
            "sites": [
                {"host": "example.com", "daily_minutes": 30},
                {"excluded": True},  # no host
                {"host": "not a host", "excluded": True},
            ],
        }
    )
    p = parse_policy(raw)
    assert len(p.windows) == 1 and p.windows[0].days == ()
    assert p.sites == (SiteRule(host="example.com", daily_minutes=30, excluded=False),)


def test_parse_normalizes_single_digit_hour() -> None:
    p = parse_policy(json.dumps({"windows": [{"start": "9:30", "end": "21:00"}]}))
    assert p.windows[0].start == "09:30"


def test_parse_dedupes_sites_by_host_last_wins() -> None:
    raw = json.dumps(
        {
            "sites": [
                {"host": "x.com", "daily_minutes": 10},
                {"host": "x.com", "excluded": True},
            ]
        }
    )
    p = parse_policy(raw)
    assert p.sites == (SiteRule(host="x.com", daily_minutes=None, excluded=True),)


# --- resolve: Global fallback + site merge ---


def test_resolve_unset_teen_falls_back_to_global() -> None:
    glob = parse_policy(json.dumps({"daily_minutes": {"default": 60}}))
    assert resolve(EMPTY, glob).daily_minutes == {"default": 60}


def test_resolve_teen_overrides_global_fields() -> None:
    teen = parse_policy(json.dumps({"daily_minutes": {"default": 30}}))
    glob = parse_policy(json.dumps({"daily_minutes": {"default": 120}, "windows": []}))
    out = resolve(teen, glob)
    assert out.daily_minutes == {"default": 30}


def test_resolve_inherits_global_windows_when_teen_has_none() -> None:
    teen = parse_policy(json.dumps({"daily_minutes": {"default": 30}}))
    glob = parse_policy(json.dumps({"windows": [{"start": "22:00", "end": "06:00"}]}))
    out = resolve(teen, glob)
    assert len(out.windows) == 1


def test_resolve_merges_sites_teen_wins_on_host() -> None:
    glob = parse_policy(
        json.dumps(
            {
                "sites": [
                    {"host": "khan.org", "excluded": True},
                    {"host": "g.com", "daily_minutes": 20},
                ]
            }
        )
    )
    teen = parse_policy(json.dumps({"sites": [{"host": "g.com", "daily_minutes": 60}]}))
    out = resolve(teen, glob)
    by_host = {s.host: s for s in out.sites}
    assert by_host["khan.org"].excluded is True
    assert by_host["g.com"].daily_minutes == 60  # teen overrode the global cap


# --- minutes_for ---


def test_minutes_for_uses_day_then_default() -> None:
    p = parse_policy(json.dumps({"daily_minutes": {"default": 120, "sat": 240}}))
    assert minutes_for(p, 5) == 240  # Saturday
    assert minutes_for(p, 0) == 120  # Monday -> default
    assert minutes_for(EMPTY, 0) is None  # unset -> no limit


# --- in_bedtime: wrap past midnight + day filter ---


def test_in_bedtime_wraps_past_midnight() -> None:
    p = parse_policy(json.dumps({"windows": [{"start": "21:00", "end": "07:00"}]}))
    assert in_bedtime(p, datetime(2026, 5, 30, 22, 30)) is True  # 22:30 -> blocked
    assert in_bedtime(p, datetime(2026, 5, 30, 6, 0)) is True  # 06:00 -> blocked
    assert in_bedtime(p, datetime(2026, 5, 30, 12, 0)) is False  # noon -> fine


def test_in_bedtime_same_day_window() -> None:
    p = parse_policy(json.dumps({"windows": [{"start": "09:00", "end": "17:00"}]}))
    assert in_bedtime(p, datetime(2026, 5, 30, 10, 0)) is True
    assert in_bedtime(p, datetime(2026, 5, 30, 18, 0)) is False


def test_in_bedtime_respects_day_filter() -> None:
    # 2026-05-30 is a Saturday (weekday()==5); window only applies Mon/Tue.
    p = parse_policy(
        json.dumps({"windows": [{"days": ["mon", "tue"], "start": "00:00", "end": "23:59"}]})
    )
    assert in_bedtime(p, datetime(2026, 5, 30, 12, 0)) is False
    # 2026-06-01 is a Monday.
    assert in_bedtime(p, datetime(2026, 6, 1, 12, 0)) is True


# --- site_rule_for: subdomain match ---


def test_site_rule_matches_exact_and_subdomain() -> None:
    p = parse_policy(json.dumps({"sites": [{"host": "khanacademy.org", "excluded": True}]}))
    assert site_rule_for(p, "https://www.khanacademy.org/math") is not None
    assert site_rule_for(p, "es.khanacademy.org") is not None
    assert site_rule_for(p, "notkhanacademy.org") is None
    assert site_rule_for(p, "example.com") is None


# --- serialize round-trip ---


def test_to_json_from_stored_round_trip() -> None:
    raw = json.dumps(
        {
            "daily_minutes": {"default": 90},
            "windows": [{"days": ["fri"], "start": "23:00", "end": "06:30"}],
            "sites": [{"host": "x.com", "daily_minutes": 15, "excluded": False}],
        }
    )
    p = parse_policy(raw, source_text="ninety minutes", updated_ts="2026-05-30T00:00:00+00:00")
    again = from_stored(to_json(p))
    assert again == p
    assert again.source_text == "ninety minutes"


# --- store ---


def test_store_missing_file_is_empty(tmp_path: Path) -> None:
    assert TimePolicyStore(str(tmp_path / "absent.json")).current() == EMPTY


def test_store_malformed_is_empty(tmp_path: Path) -> None:
    p = tmp_path / "tp.json"
    p.write_text("{ not json")
    assert TimePolicyStore(str(p)).current() == EMPTY


def test_store_set_persists_and_reloads(tmp_path: Path) -> None:
    p = tmp_path / "tp.json"
    store = TimePolicyStore(str(p))
    policy = parse_policy(json.dumps({"daily_minutes": {"default": 45}}), updated_ts="t")
    store.set(policy)
    assert store.current().daily_minutes == {"default": 45}
    # a fresh store reads the same file
    assert TimePolicyStore(str(p)).current().daily_minutes == {"default": 45}
    # on-disk JSON is the serialized shape
    assert json.loads(p.read_text())["daily_minutes"] == {"default": 45}


def test_store_hot_reloads_on_external_change(tmp_path: Path) -> None:
    p = tmp_path / "tp.json"
    store = TimePolicyStore(str(p))
    assert store.current() == EMPTY
    p.write_text(json.dumps(to_json(parse_policy(json.dumps({"daily_minutes": {"default": 10}})))))
    assert store.current().daily_minutes == {"default": 10}


def test_policy_is_immutable() -> None:
    p = TimePolicy(daily_minutes={}, windows=(), sites=(), source_text="", updated_ts="")
    with pytest.raises(AttributeError):
        p.source_text = "x"  # type: ignore[misc]
