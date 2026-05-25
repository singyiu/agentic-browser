"""Unit tests for the JSONL event log."""

from __future__ import annotations

import json
from pathlib import Path

from agent_backend.guardian.event_log import EventLog


def test_appends_one_jsonl_line(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "events.jsonl"
    EventLog(str(path)).log("block", url="u", reason="r")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "block"
    assert record["url"] == "u"
    assert "ts" in record


def test_multiple_entries(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = EventLog(str(path))
    log.log("allow")
    log.log("block")
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b" / "events.jsonl"
    EventLog(str(path)).log("x")
    assert path.exists()


# --- recent(): the parent Activity view reads events back -------------------


def _seed(path: Path, records: list[dict[str, object]]) -> EventLog:
    log = EventLog(str(path))
    for record in records:
        log.log(str(record["event"]), **{k: v for k, v in record.items() if k != "event"})
    return log


def test_recent_missing_file_is_empty(tmp_path: Path) -> None:
    # Fail safe: nothing logged yet => no activity to show (never raises).
    assert EventLog(str(tmp_path / "absent.jsonl")).recent(10) == []


def test_recent_returns_newest_first(tmp_path: Path) -> None:
    log = _seed(
        tmp_path / "e.jsonl",
        [{"event": "allow", "url": "a"}, {"event": "block", "url": "b"}],
    )
    assert [e["url"] for e in log.recent(10)] == ["b", "a"]


def test_recent_respects_limit(tmp_path: Path) -> None:
    log = _seed(tmp_path / "e.jsonl", [{"event": "allow", "url": str(i)} for i in range(5)])
    assert [e["url"] for e in log.recent(2)] == ["4", "3"]


def test_recent_filters_by_profile(tmp_path: Path) -> None:
    log = _seed(
        tmp_path / "e.jsonl",
        [
            {"event": "allow", "url": "a", "profile": "alice"},
            {"event": "block", "url": "b", "profile": "bob"},
        ],
    )
    assert [e["url"] for e in log.recent(10, profile="alice")] == ["a"]


def test_recent_filters_by_event_type(tmp_path: Path) -> None:
    log = _seed(
        tmp_path / "e.jsonl",
        [
            {"event": "allow", "url": "a"},
            {"event": "profile_created", "profile": "x"},
            {"event": "block", "url": "b"},
        ],
    )
    assert [e["event"] for e in log.recent(10, events=("allow", "block"))] == ["block", "allow"]


def test_recent_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "e.jsonl"
    log = EventLog(str(path))
    log.log("allow", url="a")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")  # a torn/partial append must not crash the reader
    log.log("block", url="b")
    assert [e["url"] for e in log.recent(10)] == ["b", "a"]


def test_recent_zero_or_negative_limit_is_empty(tmp_path: Path) -> None:
    log = _seed(tmp_path / "e.jsonl", [{"event": "allow", "url": "a"}])
    assert log.recent(0) == []
    assert log.recent(-3) == []
