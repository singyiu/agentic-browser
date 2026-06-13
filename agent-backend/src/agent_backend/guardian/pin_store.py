"""Parent-PIN credential store: a salted PBKDF2 hash on disk, with env-PIN fallback.

First-run setup writes a salted hash here so the *running* guardian picks up the new PIN
with no restart (``service._require_pin`` reads this store, not the frozen config). An
existing ``GUARDIAN_PARENT_PIN`` env value still works (back-compat): when no hash file is
present, the env PIN is the credential. The plaintext PIN is never written to disk.

A missing or malformed file means "no stored PIN" (falls back to the env PIN, else
unconfigured) — it never grants access.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from datetime import UTC, datetime
from pathlib import Path

from .fsio import atomic_write_text

_HASH_NAME = "sha256"
_ALGO = f"pbkdf2_{_HASH_NAME}"
_ITERATIONS = 600_000  # OWASP 2023 minimum for PBKDF2-HMAC-SHA256
_SALT_BYTES = 16
_MIN_LEN = 4
_MAX_LEN = 8


def validate_pin_format(pin: str) -> str | None:
    """Return an error message for an invalid PIN, or ``None`` when it is acceptable.

    Policy: ASCII digits only, 4–8 characters. ``str.isdigit()`` alone would accept
    non-ASCII digit characters (full-width/superscript), so guard with ``isascii()`` too.
    """
    if not (pin.isascii() and pin.isdigit()):
        return "PIN must contain digits only (0-9)."
    if not _MIN_LEN <= len(pin) <= _MAX_LEN:
        return f"PIN must be {_MIN_LEN} to {_MAX_LEN} digits long."
    return None


def _hash(pin: str, salt: bytes, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(_HASH_NAME, pin.encode("utf-8"), salt, iterations).hex()


class PinStore:
    """Owns the parent-PIN credential file; reads fresh each call so writes apply instantly."""

    def __init__(self, path: str, *, env_pin: str = "") -> None:
        self._path = Path(path).expanduser()
        self._env_pin = env_pin
        self._lock = threading.Lock()

    def _read(self) -> dict[str, object] | None:
        """Parse the hash file, or ``None`` if it is missing/malformed.

        Atomic writes (temp file + ``os.replace``) make a lock-free read safe: a reader
        sees either the old or the new complete file, never a torn one.
        """
        try:
            data = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        if not isinstance(data.get("iterations"), int):
            return None
        salt, digest = data.get("salt"), data.get("hash")
        if not isinstance(salt, str) or not isinstance(digest, str) or not salt or not digest:
            return None
        return data

    def is_configured(self) -> bool:
        """True when a usable PIN exists — a valid hash file, or a non-empty env PIN."""
        return self._read() is not None or bool(self._env_pin)

    def verify(self, submitted: str) -> bool:
        """Constant-time check of ``submitted`` against the stored hash (file wins) or env PIN."""
        record = self._read()
        if record is not None:
            salt = bytes.fromhex(str(record["salt"]))
            digest = _hash(submitted, salt, int(record["iterations"]))
            return hmac.compare_digest(digest, str(record["hash"]))
        if self._env_pin:
            return hmac.compare_digest(submitted, self._env_pin)
        return False

    def set_pin(self, pin: str) -> None:
        """Persist a freshly salted hash of ``pin``, replacing any existing credential."""
        salt = secrets.token_bytes(_SALT_BYTES)
        record = {
            "algo": _ALGO,
            "iterations": _ITERATIONS,
            "salt": salt.hex(),
            "hash": _hash(pin, salt, _ITERATIONS),
            "created_ts": datetime.now(UTC).isoformat(),
        }
        with self._lock:
            self._write(record)

    def _write(self, record: dict[str, object]) -> None:
        atomic_write_text(self._path, json.dumps(record, indent=2))
