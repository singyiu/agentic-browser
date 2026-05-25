"""Unit tests for the runtime profile lifecycle (ProfileManager)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_backend.guardian.profile_manager import (
    InvalidProfileNameError,
    ProfileExistsError,
    ProfileManager,
    ProfileNotFoundError,
    generate_token,
)
from agent_backend.guardian.profiles import Profile, load_profiles
from agent_backend.guardian.runtime import build_runtime

_DEFAULTS = {
    "default_token": "deftok",
    "default_whitelist_path": "w",
    "default_blocklist_path": "b",
    "default_requests_path": "r",
    "default_cache_path": "c",
}


def _manager(tmp_path: Path, *, persist: bool = True) -> ProfileManager:
    """An empty manager whose data + registry live entirely under tmp_path."""
    return ProfileManager(
        {},
        {},
        profiles_path=str(tmp_path / "profiles.json") if persist else None,
        data_dir=str(tmp_path / "profiles"),
    )


# --- generate_token ----------------------------------------------------------


def test_generate_token_is_64_hex() -> None:
    token = generate_token()
    assert len(token) == 64
    assert all(c in "0123456789abcdef" for c in token)


def test_generate_token_is_unique() -> None:
    assert generate_token() != generate_token()


# --- create ------------------------------------------------------------------


def test_create_returns_runtime_and_token(tmp_path: Path) -> None:
    runtime, token = _manager(tmp_path).create("alice")
    assert runtime.name == "alice"
    assert runtime.token == token
    assert len(token) == 64


def test_create_makes_isolated_data_dir(tmp_path: Path) -> None:
    _manager(tmp_path).create("alice")
    assert (tmp_path / "profiles" / "alice").is_dir()


def test_create_registers_in_snapshot(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.create("alice")
    assert set(mgr.snapshot()) == {"alice"}


def test_create_duplicate_name_raises(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.create("alice")
    with pytest.raises(ProfileExistsError):
        mgr.create("alice")


def test_create_orphan_data_dir_raises(tmp_path: Path) -> None:
    # A leftover dir from a prior un-purged delete must not be silently adopted.
    (tmp_path / "profiles" / "alice").mkdir(parents=True)
    with pytest.raises(ProfileExistsError):
        _manager(tmp_path).create("alice")


def test_create_bad_name_raises(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    for bad in ("a/b", "..", "", "  ", "a" * 65):
        with pytest.raises(InvalidProfileNameError):
            mgr.create(bad)


# --- list_profiles -----------------------------------------------------------


def test_list_omits_token(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.create("alice")
    listed = mgr.list_profiles()
    by_name = {p["name"]: p for p in listed}
    assert by_name["alice"] == {
        "name": "alice",
        "is_global": False,
        "whitelist_count": 0,
        "blocklist_count": 0,
        "pending_count": 0,
    }
    assert by_name["global"]["is_global"] is True
    assert all("token" not in p for p in listed)


def test_list_counts_reflect_stores(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    runtime, _ = mgr.create("alice")
    runtime.whitelist.add("example.com")
    runtime.whitelist.add("docs.python.org")
    (entry,) = [p for p in mgr.list_profiles() if p["name"] == "alice"]
    assert entry["whitelist_count"] == 2


# --- rename ------------------------------------------------------------------


def test_rename_rekeys_and_keeps_token(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    _, token = mgr.create("alice")
    mgr.rename("alice", "alicia")
    snap = mgr.snapshot()
    assert "alice" not in snap
    assert snap["alicia"].token == token


def test_rename_moves_data_dir_with_content(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    runtime, _ = mgr.create("alice")
    runtime.whitelist.add("example.com")
    mgr.rename("alice", "alicia")
    assert not (tmp_path / "profiles" / "alice").exists()
    assert (tmp_path / "profiles" / "alicia" / "whitelist.json").exists()
    assert "example.com" in mgr.snapshot()["alicia"].whitelist.current().values


def test_rename_to_existing_name_raises(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.create("alice")
    mgr.create("bob")
    with pytest.raises(ProfileExistsError):
        mgr.rename("alice", "bob")


def test_rename_unknown_raises(tmp_path: Path) -> None:
    with pytest.raises(ProfileNotFoundError):
        _manager(tmp_path).rename("ghost", "x")


def test_rename_bad_target_raises(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.create("alice")
    with pytest.raises(InvalidProfileNameError):
        mgr.rename("alice", "a/b")


def test_rename_custom_path_profile_skips_dir_move(tmp_path: Path) -> None:
    # The legacy "default" profile uses flat paths (no data/profiles/default/ dir); renaming
    # it must rekey without trying to move a directory that does not exist.
    legacy = Profile(
        "default",
        "tok",
        str(tmp_path / "legacy_wl.json"),
        str(tmp_path / "legacy_bl.json"),
        str(tmp_path / "legacy_req.json"),
        str(tmp_path / "legacy_cache.db"),
    )
    mgr = ProfileManager(
        {"default": legacy},
        {"default": build_runtime(legacy)},
        profiles_path=str(tmp_path / "profiles.json"),
        data_dir=str(tmp_path / "profiles"),
    )
    mgr.rename("default", "sam")
    snap = mgr.snapshot()
    assert "default" not in snap and "sam" in snap
    assert snap["sam"].whitelist.current().values == ()


# --- delete ------------------------------------------------------------------


def test_delete_removes_from_snapshot(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.create("alice")
    mgr.delete("alice")
    assert mgr.snapshot() == {}


def test_delete_no_purge_keeps_data_dir(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.create("alice")
    mgr.delete("alice")
    assert (tmp_path / "profiles" / "alice").exists()


def test_delete_purge_removes_data_dir(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.create("alice")
    mgr.delete("alice", purge=True)
    assert not (tmp_path / "profiles" / "alice").exists()


def test_delete_unknown_raises(tmp_path: Path) -> None:
    with pytest.raises(ProfileNotFoundError):
        _manager(tmp_path).delete("ghost")


# --- regenerate_token --------------------------------------------------------


def test_regenerate_returns_new_token(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    _, old = mgr.create("alice")
    new = mgr.regenerate_token("alice")
    assert new != old
    assert mgr.snapshot()["alice"].token == new


def test_regenerate_keeps_store_objects(tmp_path: Path) -> None:
    # Only the token changes; the live stores (and their in-memory state) are preserved.
    mgr = _manager(tmp_path)
    runtime, _ = mgr.create("alice")
    mgr.regenerate_token("alice")
    assert mgr.snapshot()["alice"].whitelist is runtime.whitelist


def test_regenerate_unknown_raises(tmp_path: Path) -> None:
    with pytest.raises(ProfileNotFoundError):
        _manager(tmp_path).regenerate_token("ghost")


# --- snapshot isolation ------------------------------------------------------


def test_snapshot_is_independent_copy(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.create("alice")
    snap = mgr.snapshot()
    snap.clear()
    assert set(mgr.snapshot()) == {"alice"}


# --- persistence -------------------------------------------------------------


def test_create_persists_and_survives_reload(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    _, a_tok = mgr.create("alice")
    _, b_tok = mgr.create("bob")
    reg = load_profiles(str(tmp_path / "profiles.json"), **_DEFAULTS)
    assert {p.name for p in reg.all()} == {"alice", "bob"}
    assert {p.token for p in reg.all()} == {a_tok, b_tok}


def test_no_profiles_path_skips_persistence(tmp_path: Path) -> None:
    mgr = _manager(tmp_path, persist=False)
    mgr.create("alice")
    assert not (tmp_path / "profiles.json").exists()


# --- Global profile ----------------------------------------------------------


def test_global_profile_exists_after_construction(tmp_path: Path) -> None:
    assert _manager(tmp_path).global_runtime().name == "global"


def test_global_not_in_token_auth_snapshot(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.create("alice")
    # Global must never be reachable by a teen token — it is not in the auth snapshot.
    assert "global" not in mgr.snapshot()


def test_global_runtime_has_a_blocklist(tmp_path: Path) -> None:
    g = _manager(tmp_path).global_runtime()
    g.blocklist.add("tiktok.com")
    assert g.blocklist.current().matches_url("https://tiktok.com") is True


def test_create_global_name_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvalidProfileNameError):
        _manager(tmp_path).create("global")


def test_rename_to_global_rejected(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.create("alice")
    with pytest.raises(InvalidProfileNameError):
        mgr.rename("alice", "global")


def test_delete_global_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProfileNotFoundError):
        _manager(tmp_path).delete("global")


def test_list_profiles_flags_global(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.create("alice")
    flags = {p["name"]: p["is_global"] for p in mgr.list_profiles()}
    assert flags == {"alice": False, "global": True}
