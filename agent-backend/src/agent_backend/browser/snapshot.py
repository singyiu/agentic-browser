"""Helpers for turning the page accessibility tree into LLM-friendly text.

Playwright 1.60 removed ``page.accessibility``; the controller uses
``locator.aria_snapshot()`` which already returns a compact YAML-like tree.
This module keeps the (pure, testable) budget-capping logic.
"""

from __future__ import annotations

DEFAULT_MAX_CHARS = 12_000
_MARKER = "\n… [snapshot truncated]"


def truncate_snapshot(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Cap a snapshot to a character budget, noting truncation.

    Keeps the head of the tree (top of the document, where primary controls
    usually live) and appends a marker so the model knows content was dropped.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if len(text) <= max_chars:
        return text
    head = text[: max(0, max_chars - len(_MARKER))]
    return head + _MARKER
