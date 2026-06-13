"""Per-profile + Global classification prompt: a single free-form guidance blob.

The classifier's built-in safety rubric ALWAYS applies; these prompts only ADD household
guidance on top (augment semantics) and can never relax the ALWAYS-BLOCK categories. Each
teen profile and the reserved Global profile owns a plain-text prompt file; a missing or
unreadable file reads as ``""`` (fails safe — no extra guidance, base rubric unchanged).

``merge`` combines the Global + per-profile guidance into one prompt section (Global first
for all children, the per-profile section governing on conflict). ``MergedPromptCache`` caches
that merged text per profile, keyed on the two file mtimes plus the child age, so a parent edit
or an age change takes effect on the next classification without re-merging every request.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from .fsio import atomic_write_text

# Age bands for the seeded default per-profile guidance (parents edit freely afterward).
_AGE_VERY_STRICT = 7
_AGE_STRICT = 12
_AGE_MODERATE = 15

_GUIDANCE_HEADER = (
    "\n\nADDITIONAL HOUSEHOLD GUIDANCE (apply ALONGSIDE the POLICY above; this guidance never "
    "relaxes the ALWAYS-BLOCK categories — adult_content, graphic_violence, self_harm, hate, "
    "illegal_dangerous — which are blocked regardless):\n"
)


class PromptStore:
    """Owns a profile's classification-prompt file (a single free-form text blob).

    Mirrors ``WhitelistStore``'s mtime-tracked, thread-safe reload. A missing or unreadable
    file reads as ``""`` so the base rubric still governs (fails safe).
    """

    def __init__(self, path: str) -> None:
        self._path = Path(path).expanduser()
        self._lock = threading.Lock()
        self._mtime = self._stat_mtime()
        self._current = self._read()

    def _stat_mtime(self) -> float | None:
        try:
            return self._path.stat().st_mtime
        except OSError:
            return None

    def _read(self) -> str:
        try:
            return self._path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _write(self, text: str) -> None:
        atomic_write_text(self._path, text)

    @property
    def mtime(self) -> float | None:
        """Last-seen file mtime (``None`` when the file is absent); used as a cache key."""
        return self._mtime

    def current(self) -> str:
        return self._current

    def reload_if_changed(self) -> bool:
        """Reload from disk when the file's mtime changed. Returns True if reloaded."""
        with self._lock:  # stat + compare + reload atomically (executor threads race here)
            mtime = self._stat_mtime()
            if mtime == self._mtime:
                return False
            self._mtime = mtime
            self._current = self._read()
        return True

    def set(self, text: str) -> None:
        """Replace the whole prompt with ``text`` (empty string clears it)."""
        with self._lock:
            self._write(text)
            self._mtime = self._stat_mtime()
            self._current = text

    def append(self, text: str, *, separator: str, max_chars: int) -> bool:
        """Append ``separator + text`` atomically, keeping the total <= ``max_chars``.

        Returns ``True`` when appended, ``False`` when there is no room (the caller reports the
        skip). The cap is enforced inside the lock so concurrent appends can never push the blob
        past ``max_chars``. Never truncates — a clipped household rule could invert its meaning
        ("block X but allow Y" -> "block X") — so an over-long append is dropped whole.
        """
        with self._lock:
            current = self._read()
            candidate = (current + separator + text) if current else text
            if len(candidate) > max_chars:
                return False
            self._write(candidate)
            self._mtime = self._stat_mtime()
            self._current = candidate
            return True


def default_global_prompt() -> str:
    """Default Global guidance: empty. Households opt in by writing their own."""
    return ""


def default_profile_prompt(age: int) -> str:
    """Age-band starting guidance for a child of ``age`` (a parent can edit it freely).

    Augments — never repeats — the built-in rubric: it nudges the borderline ``USE JUDGMENT``
    calls toward the child's maturity. The ALWAYS-BLOCK categories are unaffected.
    """
    if age <= _AGE_VERY_STRICT:
        return (
            f"This child is very young (age {age}). Lean strongly toward blocking: allow only "
            "children's educational sites, kids' games, and well-known children's media. Block "
            "open social media, chat with strangers, unmoderated user-generated video feeds, "
            "and shopping/checkout pages."
        )
    if age <= _AGE_STRICT:
        return (
            f"This child is {age}. Allow age-appropriate education, homework help, kids' games, "
            "and mainstream children's entertainment. Lean toward blocking open social media, "
            "anonymous chat, dating, and unmoderated user-generated content."
        )
    if age <= _AGE_MODERATE:
        return (
            f"This teen is {age}. Allow mainstream social media, messaging, and general "
            "entertainment. Use judgment on edgy-but-educational material, and keep blocking "
            "gambling and dating/hookup apps."
        )
    return (
        f"This teen is {age} and nearly an adult. Allow general teen and young-adult content, "
        "social media, and messaging. Continue to block only the always-block categories."
    )


def merge(*, age: int, global_text: str, profile_text: str) -> str:
    """Merge Global + per-profile guidance into one prompt section (``""`` if both empty).

    Global guidance is listed first ("for all children"); the per-profile guidance follows and
    governs on conflict (individual-always-wins). Parent text is inserted VERBATIM — never
    formatted or substituted — so a parent typing braces or ``{age}`` is inert.
    """
    sections: list[str] = []
    shared = global_text.strip()
    individual = profile_text.strip()
    if shared:
        sections.append("FOR ALL CHILDREN:\n" + shared)
    if individual:
        sections.append(
            f"FOR THIS CHILD (age {age}; this section governs where it conflicts with the "
            f"above):\n{individual}"
        )
    if not sections:
        return ""
    return _GUIDANCE_HEADER + "\n\n".join(sections)


class MergedPromptCache:
    """In-memory cache of the merged Global+profile prompt, keyed by profile name.

    The stored key is ``(global_mtime, profile_mtime, age)``; ``get`` rebuilds whenever any
    component differs, so a prompt edit (file mtime) or an age change self-invalidates on the
    next classify. ``invalidate`` / ``clear`` let the parent write path drop entries eagerly.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float | None, float | None, int, str]] = {}

    def get(
        self,
        profile: str,
        *,
        global_mtime: float | None,
        profile_mtime: float | None,
        age: int,
        build: Callable[[], str],
    ) -> str:
        key = (global_mtime, profile_mtime, age)
        with self._lock:
            cached = self._entries.get(profile)
            if cached is not None and cached[:3] == key:
                return cached[3]
        # Build outside the lock: merge is pure and cheap, and a duplicate concurrent build for
        # the same key yields the same text (last writer wins, harmless).
        merged = build()
        with self._lock:
            self._entries[profile] = (global_mtime, profile_mtime, age, merged)
        return merged

    def invalidate(self, profile: str) -> None:
        with self._lock:
            self._entries.pop(profile, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
