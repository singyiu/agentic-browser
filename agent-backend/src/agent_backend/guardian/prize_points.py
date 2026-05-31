"""Prize points: a durable per-profile balance plus redeem-for-time accounting.

Gamifies time management. A parent **grants** points to a teen; the teen **redeems**
points for bonus screen time without approval, in fixed packages at a fixed rate
(1 point = 1 minute). Balances are *permanent* (not daily): each profile's balance lives
in its own tiny JSON file — mirroring the other per-profile ``*Store`` classes — and is
the durable source of truth. Every change is *also* appended to the event log
(``prize_points_earned`` / ``prize_points_redeemed``) for the audit trail and Grafana, so
the store and the log never disagree about the current value.

The only non-balance state is the **daily self-redeem cap**, which bounds how many bonus
minutes a teen can buy per household-local day. It is derived on demand from the event log
(today's ``prize_points_redeemed`` minutes), so it needs no stored counter and survives
restarts and Prometheus counter resets for free.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from .event_log import EventLog

# --- redemption policy (fixed; see the plan's locked decisions) ---------------

POINTS_PER_MINUTE = 1
"""Conversion rate: buying one minute of bonus time costs this many points."""

REDEEM_PACKAGES_MIN: tuple[int, ...] = (15, 30, 60)
"""The bonus-time packages a teen may pick, in minutes."""

DEFAULT_DAILY_BONUS_CAP_MIN = 120
"""Default cap on bonus minutes a teen may self-redeem per household-local day."""

# Event name whose ``minutes_granted`` counts toward the daily cap.
_REDEEM_EVENT = "prize_points_redeemed"
# Generous bound on how many recent redeem events to scan for one day's total. A teen
# redeems only a handful of times a day, so this comfortably covers it (mirrors TimeLedger).
_SCAN_LIMIT = 50_000


def cost_for_minutes(minutes: int) -> int:
    """Point cost of buying ``minutes`` of bonus time (1 point = 1 minute)."""
    return minutes * POINTS_PER_MINUTE


class PrizePointStore:
    """Owns one profile's durable prize-point balance (a tiny JSON file).

    Thread-safe. Never writes on construction; a missing or corrupt file reads as balance
    ``0`` (failing safe — a teen can never gain points from a broken file). All mutations
    are atomic read-modify-writes that clamp the balance at ``>= 0`` and replace the file
    atomically, so a crash mid-write can never corrupt the stored balance.
    """

    def __init__(self, path: str) -> None:
        self._path = Path(path).expanduser()
        self._lock = threading.Lock()

    def _read(self) -> int:
        try:
            data = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return 0
        if not isinstance(data, dict):
            return 0
        value = data.get("balance")
        if isinstance(value, bool) or not isinstance(value, int):
            return 0
        return max(0, value)

    def _write(self, balance: int) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.parent / f"{self._path.name}.tmp"
        tmp.write_text(json.dumps({"balance": balance}, indent=2))
        try:
            os.replace(tmp, self._path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

    def balance(self) -> int:
        with self._lock:
            return self._read()

    def add(self, delta: int) -> int:
        """Apply a signed ``delta``, clamp at ``>= 0``, persist, and return the new balance."""
        with self._lock:
            new_balance = max(0, self._read() + int(delta))
            self._write(new_balance)
            return new_balance

    def try_spend(self, cost: int) -> int | None:
        """Atomically deduct ``cost`` iff the balance covers it.

        Returns the new balance on success, or ``None`` if the cost is negative or the
        balance is insufficient (no change in either failure case). Doing the check and the
        deduction under one lock makes a double-click race impossible.
        """
        if cost < 0:
            return None
        with self._lock:
            current = self._read()
            if current < cost:
                return None
            new_balance = current - cost
            self._write(new_balance)
            return new_balance


def redeemed_minutes_today(
    event_log: EventLog, profile: str, *, start: datetime, end: datetime
) -> int:
    """Sum a profile's prize-redeemed bonus minutes within ``[start, end)`` (UTC).

    Enforces the daily self-redeem cap. Reads the append-only event log, so it survives
    restarts without any extra stored state. ``start``/``end`` are the UTC bounds of the
    household-local day (see :meth:`time_ledger.TimeLedger.day_bounds_utc`).
    """
    total = 0
    for record in event_log.recent(_SCAN_LIMIT, profile=profile, events=(_REDEEM_EVENT,)):
        ts = _parse_ts(record.get("ts"))
        if ts is None or not (start <= ts < end):
            continue
        minutes = record.get("minutes_granted")
        if isinstance(minutes, int) and not isinstance(minutes, bool):
            total += minutes
    return total


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
