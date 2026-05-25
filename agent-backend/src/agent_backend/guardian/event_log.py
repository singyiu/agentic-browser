"""Append-only JSONL log of classification/block events."""

from __future__ import annotations

import json
import threading
from collections import deque
from collections.abc import Collection
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class EventLog:
    def __init__(self, path: str) -> None:
        self._path = Path(path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log(self, event: str, **fields: Any) -> None:
        """Append one JSON line (event = block/allow/escalate/fail_open/cache_hit)."""
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            **fields,
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._lock, self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def recent(
        self,
        limit: int,
        *,
        profile: str | None = None,
        events: Collection[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` most-recent matching records, newest first.

        Fail-safe for a live append-only file: a missing file yields ``[]`` and a
        torn/partial or otherwise malformed line is skipped rather than raised.
        Memory stays bounded by ``limit`` regardless of how large the log grows.
        """
        if limit <= 0 or not self._path.exists():
            return []
        window: deque[dict[str, Any]] = deque(maxlen=limit)
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    record: Any = json.loads(line)
                except ValueError:
                    continue  # torn append or corrupt line — skip, never crash the reader
                if not isinstance(record, dict):
                    continue
                if profile is not None and record.get("profile") != profile:
                    continue
                if events is not None and record.get("event") not in events:
                    continue
                window.append(record)
        return list(reversed(window))
