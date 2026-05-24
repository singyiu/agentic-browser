"""Runtime lifecycle for teen profiles: create, rename, delete, regenerate tokens.

The HTTP service holds one ``ProfileManager``. It owns the live ``name -> ProfileRuntime``
map (what request auth and the whitelist handlers read) alongside the canonical
``name -> Profile`` records (what gets persisted). Mutations are serialized with a lock and
written through to the profiles JSON file atomically, so a newly created teen is usable
immediately and survives a restart -- no process restart, no hand-edited config.
"""

from __future__ import annotations

import secrets
import shutil
import threading
from dataclasses import replace
from pathlib import Path

from .profiles import (
    PROFILE_DATA_DIR,
    PROFILE_NAME_RE,
    Profile,
    default_profile_paths,
    save_profiles,
)
from .runtime import ProfileRuntime, build_runtime

_TOKEN_BYTES = 32  # secrets.token_hex(32) -> 64 hex characters
_MAX_NAME_LEN = 64


class ProfileError(Exception):
    """Base class for profile lifecycle errors (the service maps these to HTTP codes)."""


class ProfileExistsError(ProfileError):
    """A profile with that name (or an orphaned data dir) already exists."""


class ProfileNotFoundError(ProfileError):
    """No profile with that name."""


class InvalidProfileNameError(ProfileError):
    """The name is not a safe filesystem slug."""


def generate_token() -> str:
    """A fresh per-browser bearer token (64 hex characters)."""
    return secrets.token_hex(_TOKEN_BYTES)


def _validate_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or len(cleaned) > _MAX_NAME_LEN or not PROFILE_NAME_RE.match(cleaned):
        raise InvalidProfileNameError(
            "Profile name must be 1-64 characters of letters, digits, '-' or '_'."
        )
    return cleaned


class ProfileManager:
    """Owns the live runtimes + canonical profile records; mutations persist atomically."""

    def __init__(
        self,
        profiles: dict[str, Profile],
        runtimes: dict[str, ProfileRuntime],
        *,
        profiles_path: str | None,
        data_dir: str = PROFILE_DATA_DIR,
    ) -> None:
        self._profiles = dict(profiles)
        self._runtimes = dict(runtimes)
        self._profiles_path = profiles_path
        self._data_dir = data_dir
        self._lock = threading.Lock()

    # --- reads ---------------------------------------------------------------

    def snapshot(self) -> dict[str, ProfileRuntime]:
        """A stable, independent copy of the live runtimes for one request's use."""
        with self._lock:
            return dict(self._runtimes)

    def list_profiles(self) -> list[dict[str, object]]:
        """Per-profile metadata for the parent UI. Never includes the token."""
        with self._lock:
            runtimes = list(self._runtimes.values())
        return [
            {
                "name": rt.name,
                "whitelist_count": len(rt.whitelist.current().values),
                "pending_count": len(rt.request_store.current().pending()),
            }
            for rt in runtimes
        ]

    # --- writes --------------------------------------------------------------

    def create(self, name: str) -> tuple[ProfileRuntime, str]:
        """Create a profile with a fresh token + isolated stores. Returns (runtime, token)."""
        cleaned = _validate_name(name)
        token = generate_token()
        wl, req, cache = default_profile_paths(cleaned, self._data_dir)
        profile = Profile(cleaned, token, wl, req, cache)
        with self._lock:
            if cleaned in self._profiles:
                raise ProfileExistsError(cleaned)
            if Path(wl).expanduser().parent.exists():
                # Orphan dir from a prior un-purged delete: refuse rather than adopt stale data.
                raise ProfileExistsError(cleaned)
            runtime = build_runtime(profile)
            self._profiles[cleaned] = profile
            self._runtimes[cleaned] = runtime
            self._save()
        return runtime, token

    def rename(self, old_name: str, new_name: str) -> None:
        """Rename a profile, moving its data dir; the token is unchanged."""
        cleaned = _validate_name(new_name)
        with self._lock:
            if old_name not in self._profiles:
                raise ProfileNotFoundError(old_name)
            if cleaned != old_name and cleaned in self._profiles:
                raise ProfileExistsError(cleaned)
            new_profile = self._relocate(self._profiles[old_name], cleaned)
            runtime = build_runtime(new_profile)
            del self._profiles[old_name]
            del self._runtimes[old_name]
            self._profiles[cleaned] = new_profile
            self._runtimes[cleaned] = runtime
            self._save()

    def delete(self, name: str, *, purge: bool = False) -> None:
        """Remove a profile; ``purge`` also deletes its per-profile data directory."""
        with self._lock:
            if name not in self._profiles:
                raise ProfileNotFoundError(name)
            del self._profiles[name]
            del self._runtimes[name]
            self._save()
            if purge:
                shutil.rmtree(Path(self._data_dir).expanduser() / name, ignore_errors=True)

    def regenerate_token(self, name: str) -> str:
        """Issue a new token for a profile, invalidating the old one. Returns the new token."""
        token = generate_token()
        with self._lock:
            if name not in self._profiles:
                raise ProfileNotFoundError(name)
            self._profiles[name] = replace(self._profiles[name], token=token)
            # Only the token changes; keep the same live stores (and their in-memory state).
            self._runtimes[name] = replace(self._runtimes[name], token=token)
            self._save()
        return token

    # --- internals -----------------------------------------------------------

    def _relocate(self, old: Profile, new_name: str) -> Profile:
        """Return the renamed profile, moving its data dir when it uses the managed layout.

        A managed-layout profile (``data_dir/<name>/...``) has its directory moved and its
        paths recomputed. A profile with custom/legacy flat paths (e.g. the env-token
        ``default``) keeps its paths -- there is no per-profile directory to move.
        """
        managed = default_profile_paths(old.name, self._data_dir)
        if (old.whitelist_path, old.requests_path, old.cache_path) == managed:
            old_dir = Path(self._data_dir).expanduser() / old.name
            new_dir = Path(self._data_dir).expanduser() / new_name
            shutil.move(str(old_dir), str(new_dir))
            wl, req, cache = default_profile_paths(new_name, self._data_dir)
            return Profile(new_name, old.token, wl, req, cache)
        return replace(old, name=new_name)

    def _save(self) -> None:
        if self._profiles_path:
            save_profiles(tuple(self._profiles.values()), self._profiles_path)
