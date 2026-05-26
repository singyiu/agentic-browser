"""Unit tests for the access-request model and store."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_backend.guardian.access_requests import (
    AccessRequest,
    RequestSnapshot,
    RequestStore,
)


def _ticking_clock(start: int = 0) -> Callable[[], datetime]:
    """A monotonic clock that advances one second per call (deterministic timestamps)."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    state = {"n": start}

    def now() -> datetime:
        state["n"] += 1
        return base + timedelta(seconds=state["n"])

    return now


def _store(tmp_path: Path) -> RequestStore:
    return RequestStore(str(tmp_path / "req.json"), now=_ticking_clock())


# --- loading: fail safe to empty ---


def test_missing_file_is_empty(tmp_path: Path) -> None:
    store = RequestStore(str(tmp_path / "absent.json"))
    assert store.current().requests == ()


def test_malformed_file_is_empty(tmp_path: Path) -> None:
    p = tmp_path / "req.json"
    p.write_text("{ not valid json")
    assert RequestStore(str(p)).current().requests == ()


def test_non_list_file_is_empty(tmp_path: Path) -> None:
    p = tmp_path / "req.json"
    p.write_text(json.dumps({"requests": []}))
    assert RequestStore(str(p)).current().requests == ()


# --- add_request ---


def test_add_request_creates_pending_record(tmp_path: Path) -> None:
    p = tmp_path / "req.json"
    store = RequestStore(str(p), now=_ticking_clock())
    req = store.add_request(
        url="https://www.example.com/x",
        url_key="https://www.example.com/x",
        host="example.com",
        reason="not suitable",
        note="for homework",
    )
    assert req.id.startswith("req_")
    assert req.status == "pending"
    assert req.created_ts.startswith("2026-01-01")
    assert req.decided_ts is None
    assert req.whitelist_entry is None
    # persisted to disk
    saved = json.loads(p.read_text())
    assert isinstance(saved, list) and saved[0]["id"] == req.id


def test_add_request_dedupes_pending_by_url_key(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.add_request(
        url="https://x.test/a", url_key="k", host="x.test", reason="r", note=""
    )
    second = store.add_request(
        url="https://x.test/a", url_key="k", host="x.test", reason="r", note="again"
    )
    assert first.id == second.id
    assert len(store.current().pending()) == 1


def test_add_request_allows_new_after_decided(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.add_request(
        url="https://x.test/a", url_key="k", host="x.test", reason="r", note=""
    )
    store.decide(first.id, decision="reject")
    second = store.add_request(
        url="https://x.test/a", url_key="k", host="x.test", reason="r", note=""
    )
    assert second.id != first.id
    assert len(store.current().pending()) == 1


# --- decide ---


def test_decide_approve_sets_entry_and_status(tmp_path: Path) -> None:
    store = _store(tmp_path)
    req = store.add_request(url="https://x.test/a", url_key="k", host="x.test", reason="r", note="")
    decided = store.decide(req.id, decision="approve", whitelist_entry="x.test/a")
    assert decided.status == "approved"
    assert decided.whitelist_entry == "x.test/a"
    assert decided.decided_ts is not None


def test_decide_reject_keeps_note_drops_entry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    req = store.add_request(url="https://x.test/a", url_key="k", host="x.test", reason="r", note="")
    decided = store.decide(req.id, decision="reject", decision_note="too risky")
    assert decided.status == "rejected"
    assert decided.decision_note == "too risky"
    assert decided.whitelist_entry is None


def test_decide_unknown_id_raises_keyerror(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(KeyError):
        store.decide("req_nope", decision="approve")


def test_decide_already_decided_raises_valueerror(tmp_path: Path) -> None:
    store = _store(tmp_path)
    req = store.add_request(url="https://x.test/a", url_key="k", host="x.test", reason="r", note="")
    store.decide(req.id, decision="approve")
    with pytest.raises(ValueError):
        store.decide(req.id, decision="reject")


def test_decide_bad_decision_raises_valueerror(tmp_path: Path) -> None:
    store = _store(tmp_path)
    req = store.add_request(url="https://x.test/a", url_key="k", host="x.test", reason="r", note="")
    with pytest.raises(ValueError):
        store.decide(req.id, decision="maybe")


# --- RequestSnapshot queries ---


def test_pending_filters_only_pending(tmp_path: Path) -> None:
    store = _store(tmp_path)
    a = store.add_request(url="https://a.test/", url_key="a", host="a.test", reason="r", note="")
    store.add_request(url="https://b.test/", url_key="b", host="b.test", reason="r", note="")
    store.decide(a.id, decision="approve", whitelist_entry="a.test")
    pending = store.current().pending()
    assert len(pending) == 1
    assert pending[0].url_key == "b"


def test_recent_decided_sorted_desc_and_limited(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ids = []
    for i in range(3):
        r = store.add_request(
            url=f"https://s{i}.test/", url_key=f"k{i}", host=f"s{i}.test", reason="r", note=""
        )
        ids.append(r.id)
    # decide in order k0, k1, k2 -> increasing decided_ts
    for rid in ids:
        store.decide(rid, decision="approve", whitelist_entry="e")
    recent = store.current().recent_decided(limit=2)
    assert len(recent) == 2
    # most recent decision first
    assert recent[0].id == ids[2]
    assert recent[1].id == ids[1]


def test_by_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    req = store.add_request(url="https://x.test/", url_key="k", host="x.test", reason="r", note="")
    got = store.current().by_id(req.id)
    assert got is not None and got.id == req.id
    assert store.current().by_id("req_missing") is None


def test_latest_for_url_key_returns_most_recent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.add_request(
        url="https://x.test/a", url_key="k", host="x.test", reason="r", note=""
    )
    store.decide(first.id, decision="reject")
    second = store.add_request(
        url="https://x.test/a", url_key="k", host="x.test", reason="r", note=""
    )
    latest = store.current().latest_for_url_key("k")
    assert latest is not None and latest.id == second.id
    assert store.current().latest_for_url_key("absent") is None


# --- persistence + concurrency ---


def test_round_trips_across_reload(tmp_path: Path) -> None:
    p = tmp_path / "req.json"
    store = RequestStore(str(p), now=_ticking_clock())
    req = store.add_request(
        url="https://x.test/", url_key="k", host="x.test", reason="r", note="hi"
    )
    reloaded = RequestStore(str(p))
    got = reloaded.current().by_id(req.id)
    assert got is not None
    assert got.note == "hi" and got.status == "pending"


def test_concurrent_add_same_key_dedupes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    barrier = threading.Barrier(10)

    def worker() -> None:
        barrier.wait()
        store.add_request(url="https://x.test/a", url_key="k", host="x.test", reason="r", note="")

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(store.current().pending()) == 1


def test_immutable_records(tmp_path: Path) -> None:
    store = _store(tmp_path)
    req = store.add_request(url="https://x.test/", url_key="k", host="x.test", reason="r", note="")
    with pytest.raises(AttributeError):  # frozen dataclass -> FrozenInstanceError(AttributeError)
        req.status = "approved"  # type: ignore[misc]


def test_accessrequest_and_snapshot_are_constructible() -> None:
    req = AccessRequest(
        id="req_1",
        url="https://x.test/",
        url_key="k",
        host="x.test",
        reason="r",
        note="",
        status="pending",
        created_ts="2026-01-01T00:00:00+00:00",
        decided_ts=None,
        decision_note=None,
        whitelist_entry=None,
    )
    snap = RequestSnapshot((req,))
    assert snap.by_id("req_1") is req


# --- search-keyword requests (kind="search") ---


def test_add_search_request_persists_kind_and_keyword(tmp_path: Path) -> None:
    p = tmp_path / "req.json"
    store = RequestStore(str(p), now=_ticking_clock())
    req = store.add_request(
        url="https://www.google.com/search?q=x",
        url_key="google.com/search",
        host="google.com",
        reason="blocked search",
        note="",
        kind="search",
        keyword="bad words",
    )
    assert req.kind == "search"
    assert req.keyword == "bad words"
    saved = json.loads(p.read_text())
    assert saved[0]["kind"] == "search" and saved[0]["keyword"] == "bad words"


def test_search_requests_dedupe_by_keyword_not_url(tmp_path: Path) -> None:
    store = _store(tmp_path)
    a = store.add_request(
        url="https://google.com/search?q=x",
        url_key="google.com/search",
        host="google.com",
        reason="r",
        note="",
        kind="search",
        keyword="bad",
    )
    # Same keyword, different page URL -> one pending ask.
    b = store.add_request(
        url="https://bing.com/search?q=x",
        url_key="bing.com/search",
        host="bing.com",
        reason="r",
        note="",
        kind="search",
        keyword="bad",
    )
    assert a.id == b.id
    assert len(store.current().pending()) == 1


def test_search_and_url_requests_do_not_cross_dedupe(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_request(url="https://x.test/", url_key="k", host="x.test", reason="r", note="")
    store.add_request(
        url="https://x.test/",
        url_key="k",
        host="x.test",
        reason="r",
        note="",
        kind="search",
        keyword="k",
    )
    assert len(store.current().pending()) == 2


def test_latest_for_keyword_finds_search_request(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_request(
        url="https://g/",
        url_key="g",
        host="g",
        reason="r",
        note="",
        kind="search",
        keyword="dragons",
    )
    found = store.current().latest_for_keyword("dragons")
    assert found is not None and found.keyword == "dragons"
    assert store.current().latest_for_keyword("unicorns") is None


def test_legacy_record_without_kind_defaults_to_url(tmp_path: Path) -> None:
    # A request file written before this feature has no kind/keyword keys.
    p = tmp_path / "req.json"
    p.write_text(
        json.dumps([{"id": "req_old", "url": "https://x/", "url_key": "x", "status": "pending"}])
    )
    rec = RequestStore(str(p)).current().by_id("req_old")
    assert rec is not None
    assert rec.kind == "url" and rec.keyword is None
