"""Unit tests for snapshot budget-capping."""

from __future__ import annotations

import pytest

from agent_backend.browser.snapshot import DEFAULT_MAX_CHARS, truncate_snapshot


def test_short_text_unchanged() -> None:
    text = '- button "OK"'
    assert truncate_snapshot(text) == text


def test_text_at_limit_unchanged() -> None:
    text = "x" * DEFAULT_MAX_CHARS
    assert truncate_snapshot(text) == text


def test_long_text_truncated_within_budget() -> None:
    text = "y" * (DEFAULT_MAX_CHARS + 5000)
    out = truncate_snapshot(text, max_chars=1000)
    assert len(out) <= 1000
    assert out.endswith("[snapshot truncated]")


def test_non_positive_budget_rejected() -> None:
    with pytest.raises(ValueError):
        truncate_snapshot("abc", max_chars=0)
