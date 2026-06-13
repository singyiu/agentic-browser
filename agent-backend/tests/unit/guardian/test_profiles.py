"""Unit tests for the teen profile registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_backend.config import ConfigError
from agent_backend.guardian.profiles import (
    PROFILE_NAME_RE,
    Profile,
    ProfileRegistry,
    default_profile_paths,
    load_profiles,
    save_profiles,
)

_DEFAULTS = {
    "default_token": "deftok",
    "default_whitelist_path": "data/guardian_whitelist.json",
    "default_blocklist_path": "data/guardian_blocklist.json",
    "default_requests_path": "data/guardian_requests.json",
    "default_cache_path": "data/guardian_cache.db",
    "default_prompt_path": "data/guardian_prompt.txt",
    "default_search_allow_path": "data/guardian_search_allow.json",
    "default_search_block_path": "data/guardian_search_block.json",
}


def _write(tmp_path: Path, data: object) -> str:
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def _registry() -> ProfileRegistry:
    return ProfileRegistry(
        (
            Profile(
                "alice",
                "tA",
                "a/wl.json",
                "a/bl.json",
                "a/req.json",
                "a/cache.db",
                "a/p.txt",
                "a/sa.json",
                "a/sb.json",
            ),
            Profile(
                "bob",
                "tB",
                "b/wl.json",
                "b/bl.json",
                "b/req.json",
                "b/cache.db",
                "b/p.txt",
                "b/sa.json",
                "b/sb.json",
            ),
        )
    )


def _find(reg: ProfileRegistry, name: str) -> Profile:
    for profile in reg.all():
        if profile.name == name:
            return profile
    raise AssertionError(f"profile {name!r} not found")


# --- ProfileRegistry container ----------------------------------------------


def test_all_returns_all_profiles() -> None:
    assert len(_registry().all()) == 2


# --- load_profiles: default fallback ----------------------------------------


def test_load_no_file_uses_default_token_and_paths(tmp_path: Path) -> None:
    reg = load_profiles(str(tmp_path / "absent.json"), **_DEFAULTS)
    assert [p.name for p in reg.all()] == ["default"]
    default = _find(reg, "default")
    assert default.token == "deftok"
    assert default.whitelist_path == "data/guardian_whitelist.json"
    assert default.blocklist_path == "data/guardian_blocklist.json"
    assert default.requests_path == "data/guardian_requests.json"
    assert default.cache_path == "data/guardian_cache.db"


def test_load_empty_list_uses_default(tmp_path: Path) -> None:
    reg = load_profiles(_write(tmp_path, []), **_DEFAULTS)
    assert [p.name for p in reg.all()] == ["default"]


# --- load_profiles: parsing --------------------------------------------------


def test_load_valid_parses_profiles(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [{"name": "alice", "token": "tA"}, {"name": "bob", "token": "tB"}],
    )
    reg = load_profiles(path, **_DEFAULTS)
    assert {p.name for p in reg.all()} == {"alice", "bob"}
    assert {p.token for p in reg.all()} == {"tA", "tB"}


def test_load_derives_default_paths_per_profile(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"name": "alice", "token": "tA"}])
    alice = _find(load_profiles(path, **_DEFAULTS), "alice")
    assert alice.whitelist_path == "data/profiles/alice/whitelist.json"
    assert alice.blocklist_path == "data/profiles/alice/blocklist.json"
    assert alice.requests_path == "data/profiles/alice/requests.json"
    assert alice.cache_path == "data/profiles/alice/cache.db"


def test_load_accepts_path_overrides(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            {
                "name": "alice",
                "token": "tA",
                "whitelist_path": "/custom/wl.json",
                "blocklist_path": "/custom/bl.json",
                "requests_path": "/custom/req.json",
                "cache_path": "/custom/cache.db",
            }
        ],
    )
    alice = _find(load_profiles(path, **_DEFAULTS), "alice")
    assert alice.whitelist_path == "/custom/wl.json"
    assert alice.blocklist_path == "/custom/bl.json"
    assert alice.requests_path == "/custom/req.json"
    assert alice.cache_path == "/custom/cache.db"


# --- load_profiles: validation ----------------------------------------------


def test_load_rejects_duplicate_tokens(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [{"name": "alice", "token": "same"}, {"name": "bob", "token": "same"}],
    )
    with pytest.raises(ConfigError, match="[Dd]uplicate"):
        load_profiles(path, **_DEFAULTS)


def test_load_rejects_duplicate_names(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [{"name": "alice", "token": "t1"}, {"name": "alice", "token": "t2"}],
    )
    with pytest.raises(ConfigError, match="[Dd]uplicate"):
        load_profiles(path, **_DEFAULTS)


def test_load_rejects_name_with_slash(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"name": "a/b", "token": "t"}])
    with pytest.raises(ConfigError):
        load_profiles(path, **_DEFAULTS)


def test_load_rejects_name_with_dotdot(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"name": "..", "token": "t"}])
    with pytest.raises(ConfigError):
        load_profiles(path, **_DEFAULTS)


def test_load_rejects_empty_name(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"name": "", "token": "t"}])
    with pytest.raises(ConfigError):
        load_profiles(path, **_DEFAULTS)


def test_load_rejects_empty_token(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"name": "alice", "token": ""}])
    with pytest.raises(ConfigError):
        load_profiles(path, **_DEFAULTS)


def test_load_no_file_no_default_token_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_profiles(
            str(tmp_path / "absent.json"),
            default_token="",
            default_whitelist_path="w",
            default_blocklist_path="b",
            default_requests_path="r",
            default_cache_path="c",
            default_prompt_path="p",
            default_search_allow_path="sa",
            default_search_block_path="sb",
        )


def test_load_empty_list_no_default_token_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, [])
    with pytest.raises(ConfigError):
        load_profiles(
            path,
            default_token="",
            default_whitelist_path="w",
            default_blocklist_path="b",
            default_requests_path="r",
            default_cache_path="c",
            default_prompt_path="p",
            default_search_allow_path="sa",
            default_search_block_path="sb",
        )


def test_load_malformed_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_profiles(str(path), **_DEFAULTS)


def test_load_non_list_json_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, {"name": "alice", "token": "t"})
    with pytest.raises(ConfigError):
        load_profiles(path, **_DEFAULTS)


# --- PROFILE_NAME_RE (public, reused by the manager/handlers) ---------------


def test_profile_name_re_accepts_slug() -> None:
    assert PROFILE_NAME_RE.match("alice_2-b")


def test_profile_name_re_rejects_separators() -> None:
    assert not PROFILE_NAME_RE.match("a/b")
    assert not PROFILE_NAME_RE.match("..")
    assert not PROFILE_NAME_RE.match("")


# --- save_profiles: round-trips with load_profiles --------------------------


def test_save_profiles_round_trip(tmp_path: Path) -> None:
    profiles = _registry().all()
    path = str(tmp_path / "out.json")
    save_profiles(profiles, path)
    reloaded = load_profiles(path, **_DEFAULTS)

    def key(p: Profile) -> tuple[object, ...]:
        return (
            p.name,
            p.token,
            p.whitelist_path,
            p.blocklist_path,
            p.requests_path,
            p.cache_path,
            p.prompt_path,
            p.age,
        )

    assert {key(p) for p in reloaded.all()} == {key(p) for p in profiles}


def test_save_profiles_creates_parent_dir(tmp_path: Path) -> None:
    path = str(tmp_path / "nested" / "dir" / "profiles.json")
    save_profiles(_registry().all(), path)
    assert Path(path).exists()
    assert {p.name for p in load_profiles(path, **_DEFAULTS).all()} == {"alice", "bob"}


def test_save_profiles_atomic_keeps_original_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = str(tmp_path / "out.json")
    save_profiles(
        (
            Profile(
                "alice",
                "tA",
                "a/wl.json",
                "a/bl.json",
                "a/req.json",
                "a/cache.db",
                "a/p.txt",
                "a/sa.json",
                "a/sb.json",
            ),
        ),
        path,
    )
    before = Path(path).read_text(encoding="utf-8")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("agent_backend.guardian.fsio.os.replace", boom)
    with pytest.raises(OSError):
        save_profiles(
            (
                Profile(
                    "bob",
                    "tB",
                    "b/wl.json",
                    "b/bl.json",
                    "b/req.json",
                    "b/cache.db",
                    "b/p.txt",
                    "b/sa.json",
                    "b/sb.json",
                ),
            ),
            path,
        )

    # A failed replace must leave the original file intact (no partial write)...
    assert Path(path).read_text(encoding="utf-8") == before
    # ...and not litter the directory with a leftover temp file.
    assert list(tmp_path.glob("*.tmp")) == []


# --- age + prompt_path (classification prompts) -----------------------------


def test_default_profile_paths_returns_seven_with_search() -> None:
    paths = default_profile_paths("alice")
    assert len(paths) == 7
    assert paths[4].endswith("/alice/prompt.txt")
    assert paths[5].endswith("/alice/search_allow.json")
    assert paths[6].endswith("/alice/search_block.json")


def test_search_paths_round_trip_save_load(tmp_path: Path) -> None:
    reg = ProfileRegistry(
        (
            Profile(
                "alice",
                "tA",
                "a/wl.json",
                "a/bl.json",
                "a/req.json",
                "a/cache.db",
                "a/p.txt",
                "a/sa.json",
                "a/sb.json",
            ),
        )
    )
    out = str(tmp_path / "p.json")
    save_profiles(reg.all(), out)
    loaded = _find(load_profiles(out, **_DEFAULTS), "alice")
    assert loaded.search_allow_path == "a/sa.json"
    assert loaded.search_block_path == "a/sb.json"


def test_load_missing_search_paths_default_to_managed(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"name": "alice", "token": "tA"}])
    alice = _find(load_profiles(path, **_DEFAULTS), "alice")
    assert alice.search_allow_path.endswith("/alice/search_allow.json")
    assert alice.search_block_path.endswith("/alice/search_block.json")


def test_load_reads_age_from_entry(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"name": "alice", "token": "tA", "age": 14}])
    assert _find(load_profiles(path, **_DEFAULTS), "alice").age == 14


def test_load_missing_age_defaults_to_10(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"name": "alice", "token": "tA"}])
    assert _find(load_profiles(path, **_DEFAULTS), "alice").age == 10


def test_load_invalid_age_defaults_to_10(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"name": "alice", "token": "tA", "age": "not-a-number"}])
    assert _find(load_profiles(path, **_DEFAULTS), "alice").age == 10


def test_load_out_of_range_age_defaults_to_10(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"name": "alice", "token": "tA", "age": 99}])
    assert _find(load_profiles(path, **_DEFAULTS), "alice").age == 10


def test_load_derives_default_prompt_path(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"name": "alice", "token": "tA"}])
    assert (
        _find(load_profiles(path, **_DEFAULTS), "alice").prompt_path
        == "data/profiles/alice/prompt.txt"
    )


def test_save_load_round_trip_preserves_age_and_prompt_path(tmp_path: Path) -> None:
    src = _write(
        tmp_path,
        [{"name": "alice", "token": "tA", "age": 13, "prompt_path": "/custom/p.txt"}],
    )
    loaded = load_profiles(src, **_DEFAULTS)
    out = str(tmp_path / "out.json")
    save_profiles(loaded.all(), out)
    alice = _find(load_profiles(out, **_DEFAULTS), "alice")
    assert alice.age == 13
    assert alice.prompt_path == "/custom/p.txt"
