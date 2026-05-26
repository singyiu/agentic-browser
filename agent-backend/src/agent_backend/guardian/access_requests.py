"""Teen access requests: a parent-reviewable queue of "please unblock this" asks.

A teen submits a request from the block page; a parent later approves (which adds the
chosen entry to the whitelist) or rejects it. Records are immutable snapshots, rebuilt
(never mutated) on every change — mirroring ``whitelist.py``. A missing or malformed
file yields an empty queue (fails safe: nothing is silently treated as approved).
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AccessRequest:
    """One teen request to access a blocked URL (immutable)."""

    id: str
    url: str
    url_key: str
    host: str
    reason: str
    note: str
    status: str
    created_ts: str
    decided_ts: str | None
    decision_note: str | None
    whitelist_entry: str | None
    kind: str = "url"  # "url" (unblock a page) | "search" (allow a search keyword)
    keyword: str | None = None  # the search term, when kind == "search"


@dataclass(frozen=True, slots=True)
class RequestSnapshot:
    """Immutable point-in-time view of the request queue."""

    requests: tuple[AccessRequest, ...]

    def pending(self) -> tuple[AccessRequest, ...]:
        """Requests still awaiting a decision, in submission order."""
        return tuple(r for r in self.requests if r.status == "pending")

    def recent_decided(self, limit: int = 50) -> tuple[AccessRequest, ...]:
        """Decided requests, most recently decided first, capped at ``limit``."""
        decided = [r for r in self.requests if r.status != "pending"]
        decided.sort(key=lambda r: r.decided_ts or "", reverse=True)
        return tuple(decided[:limit])

    def by_id(self, request_id: str) -> AccessRequest | None:
        return next((r for r in self.requests if r.id == request_id), None)

    def latest_for_url_key(self, url_key: str) -> AccessRequest | None:
        """The most recent request matching ``url_key`` (submission order = chronological)."""
        return next((r for r in reversed(self.requests) if r.url_key == url_key), None)

    def latest_for_keyword(self, keyword: str) -> AccessRequest | None:
        """The most recent search-keyword request matching ``keyword``."""
        return next(
            (r for r in reversed(self.requests) if r.kind == "search" and r.keyword == keyword),
            None,
        )


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _parse(item: object) -> AccessRequest | None:
    """Build a request from a stored dict; ``None`` if it lacks the essential keys."""
    if not isinstance(item, dict):
        return None
    request_id = item.get("id")
    url = item.get("url")
    if not isinstance(request_id, str) or not isinstance(url, str):
        return None
    return AccessRequest(
        id=request_id,
        url=url,
        url_key=str(item.get("url_key", "")),
        host=str(item.get("host", "")),
        reason=str(item.get("reason", "")),
        note=str(item.get("note", "")),
        status=str(item.get("status", "pending")),
        created_ts=str(item.get("created_ts", "")),
        decided_ts=_opt_str(item.get("decided_ts")),
        decision_note=_opt_str(item.get("decision_note")),
        whitelist_entry=_opt_str(item.get("whitelist_entry")),
        kind=str(item.get("kind", "url")),
        keyword=_opt_str(item.get("keyword")),
    )


class RequestStore:
    """Owns the requests file; thread-safe add/decide; rebuilds the snapshot on change."""

    def __init__(self, path: str, *, now: Callable[[], datetime] | None = None) -> None:
        self._path = Path(path).expanduser()
        self._lock = threading.Lock()
        self._now = now or (lambda: datetime.now(UTC))
        self._current = RequestSnapshot(self._read())

    def _read(self) -> tuple[AccessRequest, ...]:
        try:
            data = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return ()
        if not isinstance(data, list):
            return ()
        parsed = (_parse(item) for item in data)
        return tuple(r for r in parsed if r is not None)

    def _write(self, requests: Iterable[AccessRequest]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps([asdict(r) for r in requests], indent=2))

    def current(self) -> RequestSnapshot:
        return self._current

    def add_request(
        self,
        *,
        url: str,
        url_key: str,
        host: str,
        reason: str,
        note: str,
        kind: str = "url",
        keyword: str | None = None,
    ) -> AccessRequest:
        """Queue a new pending request, or return the existing pending duplicate.

        URL requests dedupe on ``url_key``; search-keyword requests dedupe on ``keyword`` (the
        same query blocked on different pages is a single ask).
        """
        with self._lock:
            requests = list(self._read())
            for existing in requests:
                if existing.status != "pending":
                    continue
                duplicate = (
                    existing.kind == "search" and existing.keyword == keyword
                    if kind == "search"
                    else existing.kind == "url" and existing.url_key == url_key
                )
                if duplicate:
                    self._current = RequestSnapshot(tuple(requests))
                    return existing
            request = AccessRequest(
                id=f"req_{uuid.uuid4().hex}",
                url=url,
                url_key=url_key,
                host=host,
                reason=reason,
                note=note,
                status="pending",
                created_ts=self._now().isoformat(),
                decided_ts=None,
                decision_note=None,
                whitelist_entry=None,
                kind=kind,
                keyword=keyword,
            )
            requests.append(request)
            self._write(requests)
            self._current = RequestSnapshot(tuple(requests))
            return request

    def decide(
        self,
        request_id: str,
        *,
        decision: str,
        whitelist_entry: str | None = None,
        decision_note: str | None = None,
    ) -> AccessRequest:
        """Approve or reject a pending request. Raises on unknown id or non-pending status."""
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
                whitelist_entry=whitelist_entry if decision == "approve" else None,
            )
            requests[index] = updated
            self._write(requests)
            self._current = RequestSnapshot(tuple(requests))
            return updated
