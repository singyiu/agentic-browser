"""Unit tests for the blocklist store (the deny-side mirror of the whitelist store)."""

from __future__ import annotations

from pathlib import Path

from agent_backend.guardian.blocklist import BlocklistStore


def test_missing_file_is_empty(tmp_path: Path) -> None:
    # Fail safe: no file => nothing is blocked.
    store = BlocklistStore(str(tmp_path / "absent.json"))
    assert store.current().values == ()
    assert store.current().matches_url("https://example.com") is False


def test_add_and_match_exact(tmp_path: Path) -> None:
    store = BlocklistStore(str(tmp_path / "bl.json"))
    store.add("tiktok.com")
    assert store.current().matches_url("https://www.tiktok.com/") is True
    assert store.current().matches_url("https://example.com") is False


def test_add_wildcard_match(tmp_path: Path) -> None:
    store = BlocklistStore(str(tmp_path / "bl.json"))
    store.add("youtube.com/watch*")
    assert store.current().matches_url("https://youtube.com/watch?v=x") is True


def test_remove_stops_matching(tmp_path: Path) -> None:
    store = BlocklistStore(str(tmp_path / "bl.json"))
    store.add("tiktok.com")
    store.remove("tiktok.com")
    assert store.current().matches_url("https://tiktok.com") is False


def test_content_entry_is_listed_but_never_matches_url(tmp_path: Path) -> None:
    store = BlocklistStore(str(tmp_path / "bl.json"))
    store.add("online gambling")
    assert store.current().matches_url("https://example.com") is False
    assert "online gambling" in store.current().content_entries


def test_reload_if_changed(tmp_path: Path) -> None:
    path = tmp_path / "bl.json"
    store = BlocklistStore(str(path))
    path.write_text('["tiktok.com"]')
    assert store.reload_if_changed() is True
    assert store.current().matches_url("https://tiktok.com") is True
