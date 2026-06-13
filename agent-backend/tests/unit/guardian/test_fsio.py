"""Unit tests for the atomic file-write helper shared by guardian data stores."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_backend.guardian.fsio import atomic_write_text


def test_creates_file_and_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deeper" / "store.json"
    atomic_write_text(target, '{"a": 1}')
    assert target.read_text() == '{"a": 1}'


def test_replaces_existing_content(tmp_path: Path) -> None:
    target = tmp_path / "store.json"
    target.write_text("old")
    atomic_write_text(target, "new")
    assert target.read_text() == "new"


def test_no_tmp_file_left_after_success(tmp_path: Path) -> None:
    target = tmp_path / "store.json"
    atomic_write_text(target, "data")
    leftovers = [p for p in tmp_path.iterdir() if p.name != "store.json"]
    assert leftovers == []


def test_failed_write_leaves_target_intact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "store.json"
    target.write_text("original")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", boom)
    with pytest.raises(OSError):
        atomic_write_text(target, "partial")
    monkeypatch.undo()
    # The original content survives and no temp file is left behind.
    assert target.read_text() == "original"
    assert [p.name for p in tmp_path.iterdir()] == ["store.json"]
