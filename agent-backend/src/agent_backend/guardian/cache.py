"""SQLite-backed verdict cache, keyed by normalized URL."""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TTL_SECONDS = 30 * 24 * 3600  # 30 days


@dataclass(frozen=True, slots=True)
class CacheEntry:
    url_key: str
    verdict: str
    reason: str
    confidence: float
    cached_at: float


class VerdictCache:
    """Thread-safe (synchronous) cache. The async service dispatches calls to an executor."""

    def __init__(
        self,
        path: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        *,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._ttl = ttl_seconds
        self._now = now
        self._lock = threading.Lock()
        Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS verdicts ("
            "url_key TEXT PRIMARY KEY, verdict TEXT NOT NULL, reason TEXT, "
            "confidence REAL, cached_at REAL NOT NULL)"
        )
        self._conn.commit()

    def get(self, url_key: str) -> CacheEntry | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT url_key, verdict, reason, confidence, cached_at "
                "FROM verdicts WHERE url_key = ?",
                (url_key,),
            ).fetchone()
        if row is None:
            return None
        entry = CacheEntry(*row)
        if self._now() - entry.cached_at > self._ttl:
            return None
        return entry

    def put(self, url_key: str, verdict: str, reason: str, confidence: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO verdicts(url_key, verdict, reason, confidence, cached_at) "
                "VALUES(?, ?, ?, ?, ?) "
                "ON CONFLICT(url_key) DO UPDATE SET "
                "verdict=excluded.verdict, reason=excluded.reason, "
                "confidence=excluded.confidence, cached_at=excluded.cached_at",
                (url_key, verdict, reason, float(confidence), self._now()),
            )
            self._conn.commit()

    def clear(self) -> None:
        """Drop all cached verdicts (used when the whitelist changes)."""
        with self._lock:
            self._conn.execute("DELETE FROM verdicts")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
