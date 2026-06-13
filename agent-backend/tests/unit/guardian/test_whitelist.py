"""Unit tests for the whitelist model and store."""

from __future__ import annotations

import json
import os
from pathlib import Path

from agent_backend.guardian.whitelist import (
    Whitelist,
    WhitelistStore,
    canonicalize_url,
    classify_entry,
)

# --- classify_entry: type auto-detection from the string's shape ---


def test_classify_exact_url() -> None:
    assert classify_entry("www.youtube.com") == "exact"


def test_classify_exact_url_with_path() -> None:
    assert classify_entry("youtube.com/results") == "exact"


def test_classify_exact_url_with_scheme() -> None:
    assert classify_entry("https://example.com/page") == "exact"


def test_classify_wildcard() -> None:
    assert classify_entry("www.youtube.com/results*") == "wildcard"


def test_classify_content_has_space() -> None:
    assert classify_entry("BeyBlade anime") == "content"


def test_classify_content_single_word_no_dot() -> None:
    assert classify_entry("khanacademy") == "content"


# --- canonicalize_url: lowercase, drop scheme/www/trailing slash ---


def test_canonicalize_strips_scheme_www_and_slash() -> None:
    assert canonicalize_url("HTTPS://WWW.YouTube.com/") == "youtube.com"


def test_canonicalize_keeps_path_lowercased() -> None:
    assert canonicalize_url("http://Example.com/Path/") == "example.com/path"


def test_canonicalize_bare_host() -> None:
    assert canonicalize_url("youtube.com") == "youtube.com"


# --- Whitelist.matches_url ---


def test_exact_matches_home_not_video() -> None:
    wl = Whitelist(["www.youtube.com"])
    assert wl.matches_url("https://www.youtube.com/") is True
    assert wl.matches_url("https://www.youtube.com/watch?v=abc") is False


def test_wildcard_matches_subpath_not_video() -> None:
    wl = Whitelist(["www.youtube.com/results*"])
    assert wl.matches_url("https://www.youtube.com/results?search_query=cats") is True
    assert wl.matches_url("https://www.youtube.com/watch?v=abc") is False


def test_host_slash_star_excludes_bare_host() -> None:
    wl = Whitelist(["youtube.com/*"])
    assert wl.matches_url("https://youtube.com/feed") is True
    assert wl.matches_url("https://youtube.com/") is False


def test_host_star_includes_bare_host_and_subpaths() -> None:
    wl = Whitelist(["youtube.com*"])
    assert wl.matches_url("https://youtube.com/") is True
    assert wl.matches_url("https://youtube.com/feed") is True


def test_content_entry_never_matches_url() -> None:
    wl = Whitelist(["BeyBlade anime"])
    assert wl.matches_url("https://anything.example.com/") is False


# --- Whitelist.content_entries / values ---


def test_content_entries_extracted() -> None:
    wl = Whitelist(["BeyBlade anime", "www.youtube.com", "Pokemon cartoon"])
    assert wl.content_entries == ("BeyBlade anime", "Pokemon cartoon")


def test_values_preserves_originals() -> None:
    wl = Whitelist(["www.youtube.com", "BeyBlade anime"])
    assert wl.values == ("www.youtube.com", "BeyBlade anime")


# --- The YouTube worked example ---


def test_youtube_worked_example() -> None:
    wl = Whitelist(["www.youtube.com", "www.youtube.com/results*", "BeyBlade anime"])
    assert wl.matches_url("https://www.youtube.com/") is True
    assert wl.matches_url("https://www.youtube.com/results?search_query=beyblade") is True
    assert wl.matches_url("https://www.youtube.com/watch?v=randomvideo") is False
    assert wl.content_entries == ("BeyBlade anime",)


# --- WhitelistStore: file IO + hot reload ---


def test_store_loads_json_array(tmp_path: Path) -> None:
    p = tmp_path / "wl.json"
    p.write_text(json.dumps(["www.youtube.com", "BeyBlade anime"]))
    store = WhitelistStore(str(p))
    assert store.current().values == ("www.youtube.com", "BeyBlade anime")


def test_store_loads_entries_object(tmp_path: Path) -> None:
    p = tmp_path / "wl.json"
    p.write_text(json.dumps({"entries": ["www.youtube.com"]}))
    store = WhitelistStore(str(p))
    assert store.current().values == ("www.youtube.com",)


def test_store_missing_file_is_empty(tmp_path: Path) -> None:
    store = WhitelistStore(str(tmp_path / "absent.json"))
    assert store.current().values == ()


def test_store_malformed_is_empty(tmp_path: Path) -> None:
    p = tmp_path / "wl.json"
    p.write_text("{ this is not valid json")
    store = WhitelistStore(str(p))
    assert store.current().values == ()


def test_store_skips_blank_entries(tmp_path: Path) -> None:
    p = tmp_path / "wl.json"
    p.write_text(json.dumps(["www.youtube.com", "  ", ""]))
    store = WhitelistStore(str(p))
    assert store.current().values == ("www.youtube.com",)


def test_store_add_persists_and_dedupes(tmp_path: Path) -> None:
    p = tmp_path / "wl.json"
    store = WhitelistStore(str(p))
    store.add("www.youtube.com")
    store.add("www.youtube.com")  # idempotent
    assert store.current().values == ("www.youtube.com",)
    assert json.loads(p.read_text()) == ["www.youtube.com"]
    # Writes are atomic (temp + replace) and clean up after themselves.
    assert not (tmp_path / "wl.json.tmp").exists()


def test_store_add_strips_entry(tmp_path: Path) -> None:
    p = tmp_path / "wl.json"
    store = WhitelistStore(str(p))
    store.add("  www.youtube.com  ")
    assert store.current().values == ("www.youtube.com",)
    assert json.loads(p.read_text()) == ["www.youtube.com"]


def test_store_remove_persists(tmp_path: Path) -> None:
    p = tmp_path / "wl.json"
    p.write_text(json.dumps(["www.youtube.com", "BeyBlade anime"]))
    store = WhitelistStore(str(p))
    store.remove("www.youtube.com")
    assert store.current().values == ("BeyBlade anime",)


def test_store_reload_if_changed(tmp_path: Path) -> None:
    p = tmp_path / "wl.json"
    p.write_text(json.dumps(["www.youtube.com"]))
    store = WhitelistStore(str(p))
    assert store.reload_if_changed() is False  # unchanged since construction

    p.write_text(json.dumps(["www.youtube.com", "BeyBlade anime"]))
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 10))  # force a distinct mtime
    assert store.reload_if_changed() is True
    assert store.current().content_entries == ("BeyBlade anime",)
