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
