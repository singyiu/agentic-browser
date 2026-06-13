"""Teen "ask for more time" requests: a parent-reviewable queue.

A teen submits a request from the time's-up block page (or the in-page HUD); a parent
later approves it with a number of bonus minutes, or rejects it with a note. Mirrors
:mod:`access_requests` exactly — immutable records rebuilt (never mutated) on every
change, a missing/malformed file yields an empty queue (fails safe: nothing is silently
granted).
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from .fsio import atomic_write_text

# A request targets either the general pool (target_host=None) or a specific site.
GENERAL_TARGET = None


@dataclass(frozen=True, slots=True)
class TimeRequest:
    """One teen request for extra screen time (immutable)."""

    id: str
    target_host: str | None  # None = general pool; else the site the kid wants more of
    requested_minutes: int | None  # the kid's ask (optional; parent decides the real grant)
    reason: str  # the kid's stated reason
    note: str  # any extra note
    status: str  # "pending" | "approved" | "rejected"
    created_ts: str
    decided_ts: str | None
    decision_note: str | None
    granted_minutes: int | None  # minutes the parent actually granted (approve only)


@dataclass(frozen=True, slots=True)
class TimeRequestSnapshot:
    """Immutable point-in-time view of the time-request queue."""

    requests: tuple[TimeRequest, ...]

    def pending(self) -> tuple[TimeRequest, ...]:
        return tuple(r for r in self.requests if r.status == "pending")

    def recent_decided(self, limit: int = 50) -> tuple[TimeRequest, ...]:
        decided = [r for r in self.requests if r.status != "pending"]
        decided.sort(key=lambda r: r.decided_ts or "", reverse=True)
        return tuple(decided[:limit])

    def by_id(self, request_id: str) -> TimeRequest | None:
        return next((r for r in self.requests if r.id == request_id), None)

    def latest(self) -> TimeRequest | None:
        """The most recent request (submission order is chronological)."""
        return self.requests[-1] if self.requests else None


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _opt_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _parse(item: object) -> TimeRequest | None:
    if not isinstance(item, dict):
        return None
    request_id = item.get("id")
    if not isinstance(request_id, str):
        return None
    return TimeRequest(
        id=request_id,
        target_host=_opt_str(item.get("target_host")),
        requested_minutes=_opt_int(item.get("requested_minutes")),
        reason=str(item.get("reason", "")),
        note=str(item.get("note", "")),
        status=str(item.get("status", "pending")),
        created_ts=str(item.get("created_ts", "")),
        decided_ts=_opt_str(item.get("decided_ts")),
        decision_note=_opt_str(item.get("decision_note")),
        granted_minutes=_opt_int(item.get("granted_minutes")),
    )


class TimeRequestStore:
    """Owns one profile's time-request file; thread-safe add/decide; rebuilds on change."""

    def __init__(self, path: str, *, now: Callable[[], datetime] | None = None) -> None:
        self._path = Path(path).expanduser()
        self._lock = threading.Lock()
        self._now = now or (lambda: datetime.now(UTC))
        self._current = TimeRequestSnapshot(self._read())

    def _read(self) -> tuple[TimeRequest, ...]:
        try:
            data = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return ()
        if not isinstance(data, list):
            return ()
        parsed = (_parse(item) for item in data)
        return tuple(r for r in parsed if r is not None)

    def _write(self, requests: Iterable[TimeRequest]) -> None:
        atomic_write_text(self._path, json.dumps([asdict(r) for r in requests], indent=2))

    def current(self) -> TimeRequestSnapshot:
        return self._current

    def add_request(
        self,
        *,
        target_host: str | None = GENERAL_TARGET,
        requested_minutes: int | None = None,
        reason: str = "",
        note: str = "",
    ) -> TimeRequest:
        """Queue a new pending request, or return the existing pending one for the same
        target (one outstanding ask per pool/site at a time)."""
        with self._lock:
            requests = list(self._read())
            for existing in requests:
                if existing.status == "pending" and existing.target_host == target_host:
                    self._current = TimeRequestSnapshot(tuple(requests))
                    return existing
            request = TimeRequest(
                id=f"treq_{uuid.uuid4().hex}",
                target_host=target_host,
                requested_minutes=requested_minutes,
                reason=reason,
                note=note,
                status="pending",
                created_ts=self._now().isoformat(),
                decided_ts=None,
                decision_note=None,
                granted_minutes=None,
            )
            requests.append(request)
            self._write(requests)
            self._current = TimeRequestSnapshot(tuple(requests))
            return request

    def decide(
        self,
        request_id: str,
        *,
        decision: str,
        granted_minutes: int | None = None,
        decision_note: str | None = None,
    ) -> TimeRequest:
        """Approve (with bonus minutes) or reject a pending request."""
        if decision not in ("approve", "reject"):
            raise ValueError(f"decision must be approve|reject, got {decision!r}")
        with self._lock:
            requests = list(self._read())
            index = next((i for i, r in enumerate(requests) if r.id == request_id), None)
            if index is None:
                raise KeyError(request_id)
            existing = requests[index]
            if existing.status != "pending":
                raise ValueError(f"request {request_id} already {existing.status}")
            updated = replace(
                existing,
                status="approved" if decision == "approve" else "rejected",
                decided_ts=self._now().isoformat(),
                decision_note=decision_note,
                granted_minutes=granted_minutes if decision == "approve" else None,
            )
            requests[index] = updated
            self._write(requests)
            self._current = TimeRequestSnapshot(tuple(requests))
            return updated
