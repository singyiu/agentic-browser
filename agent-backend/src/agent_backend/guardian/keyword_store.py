"""Search-keyword lists: whole-word, case-insensitive allow/block matching.

Unlike the whitelist (which classifies entries as URL / wildcard / content), every
search-keyword entry is a plain word or phrase matched against a search query as a whole
word, case-insensitively. So "porn" blocks "free porn videos" but not "popcorn", and "sex"
never trips on "Essex". Parent-entered text is escaped, so regex metacharacters are literal.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterable
from pathlib import Path

from .fsio import atomic_write_text


class KeywordList:
    """Immutable snapshot of keyword entries; rebuilt (never mutated) on reload."""

    def __init__(self, values: Iterable[str]) -> None:
        originals: list[str] = []
        patterns: list[re.Pattern[str]] = []
        for raw in values:
            value = raw.strip()
            if not value:
                continue
            originals.append(value)
            # re.escape neutralizes every metacharacter (literal match, no ReDoS); the
            # \w-boundary lookarounds (not \b) require the phrase to stand as a whole word.
            patterns.append(re.compile(rf"(?<!\w){re.escape(value)}(?!\w)", re.IGNORECASE))
        self._values: tuple[str, ...] = tuple(originals)
        self._patterns: tuple[re.Pattern[str], ...] = tuple(patterns)

    @property
    def values(self) -> tuple[str, ...]:
        """Original entry strings, in order, blanks dropped."""
        return self._values

    def matches(self, query: str) -> str | None:
        """Return the first entry appearing as a whole word in ``query``, else None."""
        for original, pattern in zip(self._values, self._patterns, strict=True):
            if pattern.search(query):
                return original
        return None


def _coerce_entries(data: object) -> list[str]:
    """Accept either a bare JSON array or ``{"entries": [...]}``."""
    if isinstance(data, list):
        return [str(x) for x in data]
    if isinstance(data, dict):
        entries = data.get("entries")
        if isinstance(entries, list):
            return [str(x) for x in entries]
    return []


class KeywordStore:
    """Owns a keyword-list file; hot-reloads on mtime change; supports add/remove.

    A missing or malformed file yields an empty list (fails safe: this list blocks and
    allows nothing).
    """

    def __init__(self, path: str) -> None:
        self._path = Path(path).expanduser()
        self._lock = threading.Lock()
        self._mtime = self._stat_mtime()
        self._current = KeywordList(self._read())

    def _stat_mtime(self) -> float | None:
        try:
            return self._path.stat().st_mtime
        except OSError:
            return None

    def _read(self) -> list[str]:
        try:
            data = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        return _coerce_entries(data)

    def _write(self, values: list[str]) -> None:
        atomic_write_text(self._path, json.dumps(values, indent=2))

    def current(self) -> KeywordList:
        return self._current

    def reload_if_changed(self) -> bool:
        """Reload from disk when the file's mtime changed. Returns True if reloaded."""
        with self._lock:  # stat + compare + reload atomically (executor threads race here)
            mtime = self._stat_mtime()
            if mtime == self._mtime:
                return False
            self._mtime = mtime
            self._current = KeywordList(self._read())
        return True

    def add(self, entry: str) -> None:
        entry = entry.strip()
        with self._lock:
            values = self._read()
            if entry not in values:
                values.append(entry)
            self._write(values)
            self._mtime = self._stat_mtime()
            self._current = KeywordList(values)

    def remove(self, entry: str) -> None:
        with self._lock:
            values = [v for v in self._read() if v != entry]
            self._write(values)
            self._mtime = self._stat_mtime()
            self._current = KeywordList(values)
