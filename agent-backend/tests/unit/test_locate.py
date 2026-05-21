"""Unit tests for element target resolution."""

from __future__ import annotations

import pytest

from agent_backend.browser.errors import LocateError
from agent_backend.browser.locate import resolve_target


def test_selector_takes_precedence() -> None:
    t = resolve_target(selector="#go", role="button", name="Go", text="Go")
    assert t.strategy == "selector"
    assert t.value == "#go"


def test_role_with_name() -> None:
    t = resolve_target(role="button", name="Submit")
    assert t.strategy == "role"
    assert t.value == "button"
    assert t.name == "Submit"


def test_text_fallback() -> None:
    t = resolve_target(text="Learn more")
    assert t.strategy == "text"
    assert t.value == "Learn more"


def test_blank_hints_raise() -> None:
    with pytest.raises(LocateError):
        resolve_target(selector="  ", role="", text=None)


def test_no_hints_raise() -> None:
    with pytest.raises(LocateError):
        resolve_target()
