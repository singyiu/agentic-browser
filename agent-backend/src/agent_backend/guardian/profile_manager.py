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

from .config import MAX_AGE, MIN_AGE
from .profiles import (
    GLOBAL_PROFILE_NAME,
    PROFILE_DATA_DIR,
    PROFILE_NAME_RE,
    Profile,
    default_profile_paths,
    save_profiles,
)
from .prompt import MergedPromptCache, default_profile_prompt, merge
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


class InvalidProfileAgeError(ProfileError):
    """The age is outside the accepted range."""


def generate_token() -> str:
    """A fresh per-browser bearer token (64 hex characters)."""
    return secrets.token_hex(_TOKEN_BYTES)


def _validate_name(name: str) -> str:
    cleaned = name.strip()
    if (
        not cleaned
        or len(cleaned) > _MAX_NAME_LEN
        or not PROFILE_NAME_RE.match(cleaned)
        or cleaned == GLOBAL_PROFILE_NAME
    ):
        raise InvalidProfileNameError(
            "Profile name must be 1-64 characters of letters, digits, '-' or '_', "
            f"and cannot be the reserved name {GLOBAL_PROFILE_NAME!r}."
        )
    return cleaned


def _summary(rt: ProfileRuntime, *, is_global: bool) -> dict[str, object]:
    """Token-free metadata for one profile (a kid or Global), for the parent UI."""
    return {
        "name": rt.name,
        "is_global": is_global,
        "whitelist_count": len(rt.whitelist.current().values),
        "blocklist_count": len(rt.blocklist.current().values),
        "pending_count": len(rt.request_store.current().pending()),
    }


class ProfileManager:
    """Owns the live runtimes + canonical profile records; mutations persist atomically."""

    def __init__(
        self,
        profiles: dict[str, Profile],
        runtimes: dict[str, ProfileRuntime],
        *,
        profiles_path: str | None,
        data_dir: str | None = None,
    ) -> None:
        self._profiles = dict(profiles)
        self._runtimes = dict(runtimes)
        self._profiles_path = profiles_path
        # Read the module global when no dir is given, so tests can redirect it (autouse
        # fixture) and production uses data/profiles/.
        self._data_dir = data_dir or PROFILE_DATA_DIR
        self._lock = threading.Lock()
        # The Global profile applies to every teen. It is kept OUT of _runtimes so it can
        # never be reached by an X-Guardian-Token (no browser uses it); its allow/block
        # rules persist under <data_dir>/global/ and are re-opened on each startup.
        gwl, gbl, greq, gcache, gprompt, gska, gskb = default_profile_paths(
            GLOBAL_PROFILE_NAME, self._data_dir
        )
        self._global = build_runtime(
            Profile(GLOBAL_PROFILE_NAME, "", gwl, gbl, greq, gcache, gprompt, gska, gskb)
        )
        # Cache of the merged Global+profile classification guidance, keyed by profile name.
        self._merged_cache = MergedPromptCache()

    # --- reads ---------------------------------------------------------------

    def snapshot(self) -> dict[str, ProfileRuntime]:
        """A stable, independent copy of the live runtimes for one request's use."""
        with self._lock:
            return dict(self._runtimes)

    def global_runtime(self) -> ProfileRuntime:
        """The shared Global profile (its allow + block lists apply to every teen)."""
        return self._global

    def list_profiles(self) -> list[dict[str, object]]:
        """Metadata for the parent UI: every teen plus Global. Never includes the token."""
        with self._lock:
            runtimes = list(self._runtimes.values())
        listed = [_summary(rt, is_global=False) for rt in runtimes]
        listed.append(_summary(self._global, is_global=True))
        return listed

    # --- writes --------------------------------------------------------------

    def create(self, name: str) -> tuple[ProfileRuntime, str]:
        """Create a profile with a fresh token + isolated stores. Returns (runtime, token)."""
        cleaned = _validate_name(name)
        token = generate_token()
        wl, bl, req, cache, prompt, ska, skb = default_profile_paths(cleaned, self._data_dir)
        profile = Profile(cleaned, token, wl, bl, req, cache, prompt, ska, skb)
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

    def set_age(self, name: str, age: int) -> ProfileRuntime:
        """Update a teen's age (persisted) and return the new runtime.

        Rejects the Global profile (it has no age) and out-of-range ages. Drops the profile's
        merged-prompt cache entry so the new age takes effect immediately; the verdict cache is
        cleared by the caller (the parent endpoint), matching how list edits invalidate it.
        """
        if name == GLOBAL_PROFILE_NAME:
            raise InvalidProfileNameError("The Global profile has no age.")
        if not MIN_AGE <= age <= MAX_AGE:
            raise InvalidProfileAgeError(f"Age must be between {MIN_AGE} and {MAX_AGE}.")
        with self._lock:
            if name not in self._profiles:
                raise ProfileNotFoundError(name)
            self._profiles[name] = replace(self._profiles[name], age=age)
            runtime = replace(self._runtimes[name], age=age)
            self._runtimes[name] = runtime
            self._save()
            self._merged_cache.invalidate(name)
        return runtime

    def merged_policy(self, rt: ProfileRuntime) -> str:
        """Cached merged Global+profile classification guidance for the teen runtime ``rt``.

        Falls back to the age-band default when the teen has not saved a prompt, so a fresh
        profile already classifies with age-appropriate guidance applied. The cache self-heals
        on prompt-file or age changes (it is keyed on both prompt mtimes plus the age).
        """
        shared = self._global
        return self._merged_cache.get(
            rt.name,
            global_mtime=shared.prompt_store.mtime,
            profile_mtime=rt.prompt_store.mtime,
            age=rt.age,
            build=lambda: merge(
                age=rt.age,
                global_text=shared.prompt_store.current(),
                profile_text=rt.prompt_store.current() or default_profile_prompt(rt.age),
            ),
        )

    # --- internals -----------------------------------------------------------

    def _relocate(self, old: Profile, new_name: str) -> Profile:
        """Return the renamed profile, moving its data dir when it uses the managed layout.

        A managed-layout profile (``data_dir/<name>/...``) has its directory moved and its
        paths recomputed. A profile with custom/legacy flat paths (e.g. the env-token
        ``default``) keeps its paths -- there is no per-profile directory to move.
        """
        managed = default_profile_paths(old.name, self._data_dir)
        if (
            old.whitelist_path,
            old.blocklist_path,
            old.requests_path,
            old.cache_path,
            old.prompt_path,
            old.search_allow_path,
            old.search_block_path,
        ) == managed:
            old_dir = Path(self._data_dir).expanduser() / old.name
            new_dir = Path(self._data_dir).expanduser() / new_name
            shutil.move(str(old_dir), str(new_dir))
            wl, bl, req, cache, prompt, ska, skb = default_profile_paths(new_name, self._data_dir)
            return Profile(new_name, old.token, wl, bl, req, cache, prompt, ska, skb, old.age)
        return replace(old, name=new_name)

    def _save(self) -> None:
        if self._profiles_path:
            save_profiles(tuple(self._profiles.values()), self._profiles_path)
