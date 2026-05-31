"""Unit tests for the prize-point store + daily-redeem accounting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_backend.guardian.prize_points import (
    DEFAULT_DAILY_BONUS_CAP_MIN,
    POINTS_PER_MINUTE,
    REDEEM_PACKAGES_MIN,
    PrizePointStore,
    cost_for_minutes,
    redeemed_minutes_today,
)


def test_balance_starts_at_zero_without_file(tmp_path: Path) -> None:
    assert PrizePointStore(str(tmp_path / "pp.json")).balance() == 0


def test_add_accumulates_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "pp.json"
    store = PrizePointStore(str(path))
    assert store.add(60) == 60
    assert store.add(15) == 75
    # A fresh store over the same file sees the durably persisted balance.
    assert PrizePointStore(str(path)).balance() == 75


def test_add_clamps_at_zero(tmp_path: Path) -> None:
    store = PrizePointStore(str(tmp_path / "pp.json"))
    store.add(30)
    assert store.add(-100) == 0  # a correction never drives the balance negative
    assert store.balance() == 0


def test_corrupt_file_reads_as_zero(tmp_path: Path) -> None:
    path = tmp_path / "pp.json"
    path.write_text("{not valid json")
    # Fails safe: a broken file can never be read as a positive balance.
    assert PrizePointStore(str(path)).balance() == 0


def test_try_spend_is_atomic_and_guarded(tmp_path: Path) -> None:
    store = PrizePointStore(str(tmp_path / "pp.json"))
    store.add(40)
    assert store.try_spend(30) == 10  # covered → deducts, returns new balance
    assert store.try_spend(30) is None  # insufficient → no change
    assert store.balance() == 10
    assert store.try_spend(-5) is None  # a negative cost is rejected outright
    assert store.balance() == 10


def test_cost_and_packages_match_locked_policy() -> None:
    assert POINTS_PER_MINUTE == 1
    assert REDEEM_PACKAGES_MIN == (15, 30, 60)
    assert cost_for_minutes(30) == 30  # 1 point = 1 minute
    assert DEFAULT_DAILY_BONUS_CAP_MIN >= 60


class _FakeLog:
    """A minimal EventLog stand-in with the same ``recent`` filter semantics."""

    def __init__(self, records: list[dict[str, object]]) -> None:
        self._records = records

    def recent(
        self, limit: int, *, profile: str | None = None, events: object = None
    ) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for record in self._records:
            if profile is not None and record.get("profile") != profile:
                continue
            if events is not None and record.get("event") not in events:
                continue
            out.append(record)
        return out


def test_redeemed_minutes_today_sums_only_todays_profile() -> None:
    start = datetime(2026, 5, 31, 0, 0, tzinfo=UTC)
    end = start + timedelta(days=1)
    log = _FakeLog(
        [
            {
                "ts": (start + timedelta(hours=1)).isoformat(),
                "event": "prize_points_redeemed",
                "profile": "Hei",
                "minutes_granted": 30,
            },
            {
                "ts": (start + timedelta(hours=3)).isoformat(),
                "event": "prize_points_redeemed",
                "profile": "Hei",
                "minutes_granted": 15,
            },
            {  # yesterday → excluded by the window
                "ts": (start - timedelta(hours=2)).isoformat(),
                "event": "prize_points_redeemed",
                "profile": "Hei",
                "minutes_granted": 60,
            },
            {  # another profile → excluded by the profile filter
                "ts": (start + timedelta(hours=2)).isoformat(),
                "event": "prize_points_redeemed",
                "profile": "Mei",
                "minutes_granted": 45,
            },
        ]
    )
    assert redeemed_minutes_today(log, "Hei", start=start, end=end) == 45


def test_redeemed_minutes_today_zero_when_empty() -> None:
    now = datetime(2026, 5, 31, tzinfo=UTC)
    assert redeemed_minutes_today(_FakeLog([]), "Hei", start=now, end=now + timedelta(days=1)) == 0
