"""Unit tests for the time-extension request model and store."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_backend.guardian.time_requests import (
    TimeRequest,
    TimeRequestSnapshot,
    TimeRequestStore,
)


def _ticking_clock(start: int = 0) -> Callable[[], datetime]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    state = {"n": start}

    def now() -> datetime:
        state["n"] += 1
        return base + timedelta(seconds=state["n"])

    return now


def _store(tmp_path: Path) -> TimeRequestStore:
    return TimeRequestStore(str(tmp_path / "treq.json"), now=_ticking_clock())


# --- loading: fail safe to empty ---


def test_missing_file_is_empty(tmp_path: Path) -> None:
    assert TimeRequestStore(str(tmp_path / "absent.json")).current().requests == ()


def test_malformed_file_is_empty(tmp_path: Path) -> None:
    p = tmp_path / "treq.json"
    p.write_text("{ not valid json")
    assert TimeRequestStore(str(p)).current().requests == ()


# --- add_request ---


def test_add_request_creates_pending(tmp_path: Path) -> None:
    p = tmp_path / "treq.json"
    store = TimeRequestStore(str(p), now=_ticking_clock())
    req = store.add_request(requested_minutes=30, reason="homework", note="essay due")
    assert req.id.startswith("treq_")
    assert req.status == "pending"
    assert req.target_host is None
    assert req.requested_minutes == 30
    assert req.granted_minutes is None
    saved = json.loads(p.read_text())
    assert isinstance(saved, list) and saved[0]["id"] == req.id


def test_add_request_dedupes_pending_by_target(tmp_path: Path) -> None:
    store = _store(tmp_path)
    a = store.add_request(reason="r")
    b = store.add_request(reason="again")
    assert a.id == b.id
    assert len(store.current().pending()) == 1


def test_add_request_distinct_targets_coexist(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_request(target_host=None, reason="general")
    store.add_request(target_host="g.com", reason="game")
    assert len(store.current().pending()) == 2


def test_add_request_allows_new_after_decided(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.add_request(reason="r")
    store.decide(first.id, decision="reject")
    second = store.add_request(reason="r")
    assert second.id != first.id
    assert len(store.current().pending()) == 1


# --- decide ---


def test_decide_approve_sets_granted_and_status(tmp_path: Path) -> None:
    store = _store(tmp_path)
    req = store.add_request(requested_minutes=30, reason="r")
    decided = store.decide(req.id, decision="approve", granted_minutes=15)
    assert decided.status == "approved"
    assert decided.granted_minutes == 15
    assert decided.decided_ts is not None


def test_decide_reject_keeps_note_drops_grant(tmp_path: Path) -> None:
    store = _store(tmp_path)
    req = store.add_request(reason="r")
    decided = store.decide(req.id, decision="reject", granted_minutes=15, decision_note="not now")
    assert decided.status == "rejected"
    assert decided.decision_note == "not now"
    assert decided.granted_minutes is None  # grant dropped on reject


def test_decide_unknown_id_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(KeyError):
        store.decide("treq_nope", decision="approve", granted_minutes=10)


def test_decide_already_decided_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    req = store.add_request(reason="r")
    store.decide(req.id, decision="approve", granted_minutes=10)
    with pytest.raises(ValueError):
        store.decide(req.id, decision="reject")


def test_decide_bad_decision_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    req = store.add_request(reason="r")
    with pytest.raises(ValueError):
        store.decide(req.id, decision="maybe")


# --- snapshot queries ---


def test_snapshot_pending_recent_by_id_latest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    a = store.add_request(target_host="a.com", reason="r")
    b = store.add_request(target_host="b.com", reason="r")
    store.decide(a.id, decision="approve", granted_minutes=5)
    snap = store.current()
    assert [r.id for r in snap.pending()] == [b.id]
    assert snap.recent_decided()[0].id == a.id
    assert snap.by_id(a.id) is not None
    assert snap.latest() is not None and snap.latest().id == b.id


# --- persistence + immutability ---


def test_round_trips_across_reload(tmp_path: Path) -> None:
    p = tmp_path / "treq.json"
    store = TimeRequestStore(str(p), now=_ticking_clock())
    req = store.add_request(requested_minutes=20, reason="r", note="hi")
    reloaded = TimeRequestStore(str(p))
    got = reloaded.current().by_id(req.id)
    assert got is not None and got.note == "hi" and got.requested_minutes == 20


def test_immutable_records(tmp_path: Path) -> None:
    store = _store(tmp_path)
    req = store.add_request(reason="r")
    with pytest.raises(AttributeError):
        req.status = "approved"  # type: ignore[misc]


def test_constructible() -> None:
    req = TimeRequest(
        id="treq_1",
        target_host=None,
        requested_minutes=30,
        reason="r",
        note="",
        status="pending",
        created_ts="2026-01-01T00:00:00+00:00",
        decided_ts=None,
        decision_note=None,
        granted_minutes=None,
    )
    snap = TimeRequestSnapshot((req,))
    assert snap.by_id("treq_1") is req
