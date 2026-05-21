"""Pure element-targeting logic shared by the browser controller.

The controller turns a :class:`Target` into a Playwright locator; choosing the
strategy and validating that *something* was provided is pure and unit-tested
here, independent of a live browser.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import LocateError


@dataclass(frozen=True, slots=True)
class Target:
    """A single resolved targeting strategy for an element."""

    strategy: str  # "selector" | "role" | "text"
    value: str
    name: str | None = None  # accessible name, used when strategy == "role"


def resolve_target(
    *,
    selector: str | None = None,
    role: str | None = None,
    name: str | None = None,
    text: str | None = None,
) -> Target:
    """Pick one locating strategy from the provided hints.

    Precedence: ``selector`` > ``role`` (+ optional ``name``) > ``text``.
    Raises :class:`LocateError` if no usable hint is supplied.
    """
    if selector and selector.strip():
        return Target("selector", selector.strip())
    if role and role.strip():
        return Target("role", role.strip(), name=(name.strip() if name else None))
    if text and text.strip():
        return Target("text", text.strip())
    raise LocateError(
        "No element target provided. Supply one of: selector (CSS), "
        "role (+ optional name), or text."
    )
