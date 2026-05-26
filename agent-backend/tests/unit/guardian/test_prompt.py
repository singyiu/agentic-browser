"""Unit tests for the classification-prompt store, defaults, merge, and cache."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agent_backend.guardian.prompt import (
    MergedPromptCache,
    PromptStore,
    default_global_prompt,
    default_profile_prompt,
    merge,
)

# --- PromptStore (mirrors WhitelistStore: mtime-tracked, fail-safe empty) ----


def test_prompt_store_missing_file_is_empty(tmp_path: Path) -> None:
    # Fail safe: nothing saved yet => no extra guidance, base rubric still protects.
    assert PromptStore(str(tmp_path / "absent.txt")).current() == ""


def test_prompt_store_set_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "p.txt"
    store = PromptStore(str(path))
    store.set("no anonymous chat rooms")
    assert store.current() == "no anonymous chat rooms"
    assert path.read_text(encoding="utf-8") == "no anonymous chat rooms"


def test_prompt_store_set_creates_parent_dirs(tmp_path: Path) -> None:
    store = PromptStore(str(tmp_path / "sub" / "p.txt"))
    store.set("x")
    assert (tmp_path / "sub" / "p.txt").exists()


def test_prompt_store_reload_if_changed_detects_external_write(tmp_path: Path) -> None:
    path = tmp_path / "p.txt"
    store = PromptStore(str(path))
    assert store.current() == ""
    path.write_text("edited out of band", encoding="utf-8")
    assert store.reload_if_changed() is True
    assert store.current() == "edited out of band"
    assert store.reload_if_changed() is False  # unchanged the second time


def test_prompt_store_mtime_none_until_written(tmp_path: Path) -> None:
    store = PromptStore(str(tmp_path / "p.txt"))
    assert store.mtime is None
    store.set("x")
    assert store.mtime is not None


# --- defaults ----------------------------------------------------------------


def test_default_global_prompt_is_empty() -> None:
    # Households opt in; the Global prompt is empty until a parent writes one.
    assert default_global_prompt() == ""


def test_default_profile_prompt_is_nonempty_and_mentions_age() -> None:
    for age in (5, 10, 14, 17):
        out = default_profile_prompt(age)
        assert out.strip()
        assert str(age) in out


def test_default_profile_prompt_varies_by_age_band() -> None:
    assert default_profile_prompt(6) != default_profile_prompt(16)


# --- merge -------------------------------------------------------------------


def test_merge_both_empty_returns_empty() -> None:
    assert merge(age=10, global_text="", profile_text="") == ""
    assert merge(age=10, global_text="   ", profile_text="\n") == ""


def test_merge_global_only_has_for_all_children() -> None:
    out = merge(age=10, global_text="no shopping checkout", profile_text="")
    assert "FOR ALL CHILDREN" in out
    assert "no shopping checkout" in out
    assert "FOR THIS CHILD" not in out


def test_merge_profile_only_has_for_this_child() -> None:
    out = merge(age=12, global_text="", profile_text="allow coding tutorials")
    assert "FOR THIS CHILD" in out
    assert "allow coding tutorials" in out
    assert "FOR ALL CHILDREN" not in out


def test_merge_both_global_before_profile() -> None:
    out = merge(age=12, global_text="global rule", profile_text="kid rule")
    assert out.index("FOR ALL CHILDREN") < out.index("FOR THIS CHILD")
    assert out.index("global rule") < out.index("kid rule")


def test_merge_states_it_never_relaxes_always_block() -> None:
    out = merge(age=12, global_text="g", profile_text="p")
    assert "ALWAYS-BLOCK" in out


def test_merge_inserts_parent_text_verbatim() -> None:
    # A parent typing a brace token must be inert (no str.format applied to parent text).
    out = merge(age=12, global_text="use {age} wisely", profile_text="")
    assert "use {age} wisely" in out


# --- MergedPromptCache (keyed on global_mtime, profile_mtime, age) -----------


def _counting_build() -> tuple[Callable[[], str], list[int]]:
    calls = [0]

    def build() -> str:
        calls[0] += 1
        return "MERGED"

    return build, calls


def test_merged_cache_builds_once_then_hits() -> None:
    cache = MergedPromptCache()
    build, calls = _counting_build()
    args = {"global_mtime": 1.0, "profile_mtime": 2.0, "age": 10, "build": build}
    assert cache.get("alice", **args) == "MERGED"
    assert cache.get("alice", **args) == "MERGED"
    assert calls[0] == 1


def test_merged_cache_misses_on_global_mtime_change() -> None:
    cache = MergedPromptCache()
    build, calls = _counting_build()
    cache.get("alice", global_mtime=1.0, profile_mtime=2.0, age=10, build=build)
    cache.get("alice", global_mtime=9.0, profile_mtime=2.0, age=10, build=build)
    assert calls[0] == 2


def test_merged_cache_misses_on_profile_mtime_change() -> None:
    cache = MergedPromptCache()
    build, calls = _counting_build()
    cache.get("alice", global_mtime=1.0, profile_mtime=2.0, age=10, build=build)
    cache.get("alice", global_mtime=1.0, profile_mtime=8.0, age=10, build=build)
    assert calls[0] == 2


def test_merged_cache_misses_on_age_change() -> None:
    cache = MergedPromptCache()
    build, calls = _counting_build()
    cache.get("alice", global_mtime=1.0, profile_mtime=2.0, age=10, build=build)
    cache.get("alice", global_mtime=1.0, profile_mtime=2.0, age=14, build=build)
    assert calls[0] == 2


def test_merged_cache_invalidate_forces_rebuild() -> None:
    cache = MergedPromptCache()
    build, calls = _counting_build()
    args = {"global_mtime": 1.0, "profile_mtime": 2.0, "age": 10, "build": build}
    cache.get("alice", **args)
    cache.invalidate("alice")
    cache.get("alice", **args)
    assert calls[0] == 2


def test_merged_cache_clear_forces_rebuild_for_all_profiles() -> None:
    cache = MergedPromptCache()
    build, calls = _counting_build()
    args = {"global_mtime": 1.0, "profile_mtime": 2.0, "age": 10, "build": build}
    cache.get("alice", **args)
    cache.get("bob", **args)
    cache.clear()
    cache.get("alice", **args)
    cache.get("bob", **args)
    assert calls[0] == 4
