"""Parental whitelist: hard URL rules (exact/wildcard) + soft content topics.

Entry type is auto-detected from the string's shape:
- contains ``*``             -> wildcard URL rule  (e.g. ``www.youtube.com/results*``)
- else looks like a host/URL -> exact URL rule    (e.g. ``www.youtube.com``)
- else                       -> content rule      (e.g. ``BeyBlade anime``)

URL rules match the raw page URL (what the parent writes), canonicalized for a
forgiving, case-insensitive comparison. Content rules are surfaced to the classifier
prompt as parent-approved topics; they never match URLs directly.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

EntryType = Literal["exact", "wildcard", "content"]

_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*://")
_HOST = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")


def canonicalize_url(value: str) -> str:
    """Lowercase and drop scheme, a leading ``www.``, and the trailing ``/``."""
    v = value.strip().lower()
    v = _SCHEME.sub("", v)
    v = v.removeprefix("www.")
    return v.rstrip("/")


def _looks_like_url(value: str) -> bool:
    if any(ch.isspace() for ch in value):
        return False
    host = canonicalize_url(value).split("/", 1)[0]
    return _HOST.fullmatch(host) is not None


def classify_entry(value: str) -> EntryType:
    """Detect an entry's type from its shape (``*`` -> wildcard, host-like -> exact)."""
    v = value.strip()
    if "*" in v:
        return "wildcard"
    if _looks_like_url(v):
        return "exact"
    return "content"


def _compile_wildcard(value: str) -> re.Pattern[str]:
    # re.escape FIRST neutralizes every regex metacharacter, THEN we turn the (now-escaped)
    # ``\*`` back into ``.*``. The order matters: a parent entry can never inject arbitrary
    # regex (no ReDoS, no metachar surprises) — only ``*`` becomes a wildcard.
    pattern = re.escape(canonicalize_url(value)).replace(r"\*", ".*")
    return re.compile(f"^{pattern}$")


class Whitelist:
    """Immutable snapshot of whitelist entries; rebuilt (never mutated) on reload."""

    def __init__(self, values: Iterable[str]) -> None:
        originals: list[str] = []
        exact: set[str] = set()
        wildcards: list[re.Pattern[str]] = []
        content: list[str] = []
        for raw in values:
            value = raw.strip()
            if not value:
                continue
            originals.append(value)
            kind = classify_entry(value)
            if kind == "exact":
                exact.add(canonicalize_url(value))
            elif kind == "wildcard":
                wildcards.append(_compile_wildcard(value))
            else:
                content.append(value)
        self._values: tuple[str, ...] = tuple(originals)
        self._exact: frozenset[str] = frozenset(exact)
        self._wildcards: tuple[re.Pattern[str], ...] = tuple(wildcards)
        self._content: tuple[str, ...] = tuple(content)

    @property
    def values(self) -> tuple[str, ...]:
        """Original entry strings, in order, blanks dropped."""
        return self._values

    @property
    def content_entries(self) -> tuple[str, ...]:
        """Natural-language topics to surface to the classifier prompt."""
        return self._content

    def matches_url(self, raw_url: str) -> bool:
        """True when the URL matches an exact or wildcard rule."""
        canon = canonicalize_url(raw_url)
        if canon in self._exact:
            return True
        return any(pattern.fullmatch(canon) for pattern in self._wildcards)


def _coerce_entries(data: object) -> list[str]:
    """Accept either a bare JSON array or ``{"entries": [...]}``."""
    if isinstance(data, list):
        return [str(x) for x in data]
    if isinstance(data, dict):
        entries = data.get("entries")
        if isinstance(entries, list):
            return [str(x) for x in entries]
    return []


class WhitelistStore:
    """Owns the whitelist file; hot-reloads on mtime change; supports add/remove.

    A missing or malformed file yields an empty whitelist (fails safe: everything is
    classified normally, nothing is wrongly allowed).
    """

    def __init__(self, path: str) -> None:
        self._path = Path(path).expanduser()
        self._lock = threading.Lock()
        self._mtime = self._stat_mtime()
        self._current = Whitelist(self._read())

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
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(values, indent=2))

    def current(self) -> Whitelist:
        return self._current

    def reload_if_changed(self) -> bool:
        """Reload from disk when the file's mtime changed. Returns True if reloaded."""
        with self._lock:  # stat + compare + reload atomically (executor threads race here)
            mtime = self._stat_mtime()
            if mtime == self._mtime:
                return False
            self._mtime = mtime
            self._current = Whitelist(self._read())
        return True

    def add(self, entry: str) -> None:
        entry = entry.strip()
        with self._lock:
            values = self._read()
            if entry not in values:
                values.append(entry)
            self._write(values)
            self._mtime = self._stat_mtime()
            self._current = Whitelist(values)

    def remove(self, entry: str) -> None:
        with self._lock:
            values = [v for v in self._read() if v != entry]
            self._write(values)
            self._mtime = self._stat_mtime()
            self._current = Whitelist(values)
