"""Unit tests for the SQLite verdict cache."""

from __future__ import annotations

from pathlib import Path

from agent_backend.guardian.cache import VerdictCache


def test_miss_on_empty(tmp_path: Path) -> None:
    cache = VerdictCache(str(tmp_path / "c.db"))
    assert cache.get("k") is None


def test_put_then_get(tmp_path: Path) -> None:
    cache = VerdictCache(str(tmp_path / "c.db"))
    cache.put("k", "block", "bad", 0.9)
    entry = cache.get("k")
    assert entry is not None
    assert entry.verdict == "block"
    assert entry.confidence == 0.9


def test_ttl_expiry(tmp_path: Path) -> None:
    clock = {"t": 1000.0}
    cache = VerdictCache(str(tmp_path / "c.db"), ttl_seconds=10, now=lambda: clock["t"])
    cache.put("k", "allow", "", 0.5)
    clock["t"] = 1005.0
    assert cache.get("k") is not None  # within TTL
    clock["t"] = 1100.0
    assert cache.get("k") is None  # expired


def test_upsert(tmp_path: Path) -> None:
    cache = VerdictCache(str(tmp_path / "c.db"))
    cache.put("k", "allow", "", 0.1)
    cache.put("k", "block", "bad", 0.9)
    entry = cache.get("k")
    assert entry is not None
    assert entry.verdict == "block"


def test_clear_empties_cache(tmp_path: Path) -> None:
    cache = VerdictCache(str(tmp_path / "c.db"))
    cache.put("a", "block", "bad", 0.9)
    cache.put("b", "allow", "", 0.5)
    cache.clear()
    assert cache.get("a") is None
    assert cache.get("b") is None
