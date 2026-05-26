"""Unit tests for the search-keyword store and whole-word matcher."""

from __future__ import annotations

import json
import os
from pathlib import Path

from agent_backend.guardian.keyword_store import KeywordList, KeywordStore

# --- KeywordList.matches: whole-word, case-insensitive ---


def test_matches_whole_word_in_phrase() -> None:
    assert KeywordList(["porn"]).matches("free porn videos") == "porn"


def test_matches_returns_none_when_absent() -> None:
    assert KeywordList(["porn"]).matches("popcorn") is None


def test_matches_whole_word_not_substring() -> None:
    # The classic false-positive: "sex" is a substring of "Essex" but not a whole word.
    assert KeywordList(["sex"]).matches("Essex") is None


def test_matches_phrase_at_start() -> None:
    assert KeywordList(["sex"]).matches("sex education") == "sex"


def test_matches_followed_by_punctuation() -> None:
    assert KeywordList(["porn"]).matches("is porn? bad") == "porn"


def test_matches_case_insensitive_query() -> None:
    assert KeywordList(["porn"]).matches("PORN site") == "porn"


def test_matches_case_insensitive_entry() -> None:
    # The original entry string is returned, regardless of case.
    assert KeywordList(["PORN"]).matches("porn site") == "PORN"


def test_matches_multiword_phrase() -> None:
    assert KeywordList(["buy drugs"]).matches("how to buy drugs online") == "buy drugs"


def test_matches_multiword_phrase_requires_full_phrase() -> None:
    assert KeywordList(["buy drugs"]).matches("buy milk") is None


def test_matches_none_when_no_entries() -> None:
    assert KeywordList([]).matches("anything at all") is None


def test_matches_empty_query() -> None:
    assert KeywordList(["porn"]).matches("") is None


def test_matches_returns_first_of_multiple() -> None:
    assert KeywordList(["alpha", "beta"]).matches("walk the beta path") == "beta"


def test_matches_regex_metachars_are_literal() -> None:
    # A parent entry containing regex metacharacters must match literally, never as a pattern.
    assert KeywordList(["c++"]).matches("learn c++ today") == "c++"
    assert KeywordList(["a.b"]).matches("axb") is None


def test_values_preserves_originals_and_drops_blanks() -> None:
    assert KeywordList(["porn", "  ", ""]).values == ("porn",)


# --- KeywordStore: file IO + hot reload (mirrors WhitelistStore) ---


def test_store_missing_file_is_empty(tmp_path: Path) -> None:
    store = KeywordStore(str(tmp_path / "absent.json"))
    assert store.current().values == ()
    assert store.current().matches("anything") is None


def test_store_loads_json_array(tmp_path: Path) -> None:
    p = tmp_path / "kw.json"
    p.write_text(json.dumps(["porn", "gambling"]))
    store = KeywordStore(str(p))
    assert store.current().values == ("porn", "gambling")


def test_store_loads_entries_object(tmp_path: Path) -> None:
    p = tmp_path / "kw.json"
    p.write_text(json.dumps({"entries": ["porn"]}))
    store = KeywordStore(str(p))
    assert store.current().values == ("porn",)


def test_store_malformed_is_empty(tmp_path: Path) -> None:
    p = tmp_path / "kw.json"
    p.write_text("{ not valid json")
    store = KeywordStore(str(p))
    assert store.current().values == ()


def test_store_add_persists_and_dedupes(tmp_path: Path) -> None:
    p = tmp_path / "kw.json"
    store = KeywordStore(str(p))
    store.add("gambling")
    store.add("gambling")  # idempotent
    assert store.current().values == ("gambling",)
    assert json.loads(p.read_text()) == ["gambling"]


def test_store_add_strips_entry(tmp_path: Path) -> None:
    p = tmp_path / "kw.json"
    store = KeywordStore(str(p))
    store.add("  gambling  ")
    assert store.current().values == ("gambling",)


def test_store_remove_persists(tmp_path: Path) -> None:
    p = tmp_path / "kw.json"
    p.write_text(json.dumps(["porn", "gambling"]))
    store = KeywordStore(str(p))
    store.remove("porn")
    assert store.current().values == ("gambling",)


def test_store_reload_if_changed(tmp_path: Path) -> None:
    p = tmp_path / "kw.json"
    p.write_text(json.dumps(["porn"]))
    store = KeywordStore(str(p))
    assert store.reload_if_changed() is False  # unchanged since construction

    p.write_text(json.dumps(["porn", "gambling"]))
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 10))  # force a distinct mtime
    assert store.reload_if_changed() is True
    assert store.current().matches("online gambling now") == "gambling"
