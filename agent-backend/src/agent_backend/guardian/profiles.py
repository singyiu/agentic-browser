"""Teen profile registry.

One guardian backend can govern several teens, each on a different LAN computer. A
profile bundles a teen's per-browser token with the file paths of that teen's isolated
whitelist, access-request queue, and verdict cache. The service resolves an incoming
``X-Guardian-Token`` to its profile, so one teen can never read or change another's rules.

The registry is parsed once at startup from a JSON file (default
``data/guardian_profiles.json``); with no file (or an empty list) the backend runs as a
single ``"default"`` profile on the legacy paths, byte-identical to the single-machine setup.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ..config import ConfigError

DEFAULT_PROFILE_NAME = "default"
_PROFILE_DATA_DIR = "data/profiles"
# A profile name becomes a filesystem directory component, so keep it to a safe slug
# (rejects path separators and ".."). Public so the manager and HTTP handlers validate
# the same way the loader does.
PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True, slots=True)
class Profile:
    """One teen: a token plus the paths of that teen's isolated stores."""

    name: str
    token: str
    whitelist_path: str
    requests_path: str
    cache_path: str


@dataclass(frozen=True, slots=True)
class ProfileRegistry:
    """An immutable, validated set of profiles. Token→profile resolution lives in the service."""

    profiles: tuple[Profile, ...]

    def all(self) -> tuple[Profile, ...]:
        return self.profiles


def load_profiles(
    profiles_path: str,
    *,
    default_token: str,
    default_whitelist_path: str,
    default_requests_path: str,
    default_cache_path: str,
) -> ProfileRegistry:
    """Build the registry from the JSON file, or a single default profile.

    Fails fast with ``ConfigError`` rather than ever running with no authentication.
    """
    path = Path(profiles_path).expanduser() if profiles_path else None
    if path is not None and path.exists():
        entries = _parse(path)
        if entries:
            profiles = tuple(_build_profile(entry) for entry in entries)
            _check_unique(profiles)
            return ProfileRegistry(profiles)

    # No profiles file (or an empty list): fall back to a single default profile.
    if not default_token:
        raise ConfigError(
            "No teen profiles configured and GUARDIAN_TOKEN is empty. Set GUARDIAN_TOKEN "
            "for a single profile, or create the profiles file (GUARDIAN_PROFILES_PATH)."
        )
    return ProfileRegistry(
        (
            Profile(
                name=DEFAULT_PROFILE_NAME,
                token=default_token,
                whitelist_path=default_whitelist_path,
                requests_path=default_requests_path,
                cache_path=default_cache_path,
            ),
        )
    )


def save_profiles(profiles: Iterable[Profile], path: str) -> None:
    """Persist profiles to the JSON file, atomically.

    Writes a sibling temp file then ``os.replace``s it over the target, so a crash
    mid-write can never leave a partial or corrupt registry. The serialized schema
    mirrors what :func:`load_profiles` reads, so the two round-trip exactly.
    """
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = [_profile_to_dict(p) for p in profiles]
    tmp = target.parent / f"{target.name}.tmp"
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.replace(tmp, target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _profile_to_dict(profile: Profile) -> dict[str, str]:
    return {
        "name": profile.name,
        "token": profile.token,
        "whitelist_path": profile.whitelist_path,
        "requests_path": profile.requests_path,
        "cache_path": profile.cache_path,
    }


def _parse(path: Path) -> list[object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Could not read guardian profiles file {path}: {exc}") from exc
    if not isinstance(data, list):
        raise ConfigError(f"Guardian profiles file {path} must be a JSON list of objects.")
    return data


def _build_profile(entry: object) -> Profile:
    if not isinstance(entry, dict):
        raise ConfigError("Each guardian profile must be a JSON object with name and token.")
    name = str(entry.get("name", "")).strip()
    token = str(entry.get("token", "")).strip()
    if not PROFILE_NAME_RE.match(name):
        raise ConfigError(f"Invalid profile name {name!r}: use only letters, digits, '-' or '_'.")
    if not token:
        raise ConfigError(f"Profile {name!r} has an empty token.")
    base = f"{_PROFILE_DATA_DIR}/{name}"
    return Profile(
        name=name,
        token=token,
        whitelist_path=str(entry.get("whitelist_path") or f"{base}/whitelist.json"),
        requests_path=str(entry.get("requests_path") or f"{base}/requests.json"),
        cache_path=str(entry.get("cache_path") or f"{base}/cache.db"),
    )


def _check_unique(profiles: tuple[Profile, ...]) -> None:
    names = [p.name for p in profiles]
    if len(set(names)) != len(names):
        raise ConfigError("Duplicate profile name in the guardian profiles file.")
    tokens = [p.token for p in profiles]
    if len(set(tokens)) != len(tokens):
        raise ConfigError("Duplicate profile token in the guardian profiles file.")
