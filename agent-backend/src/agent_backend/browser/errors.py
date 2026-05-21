"""Typed errors for the browser-control layer."""

from __future__ import annotations


class BrowserError(RuntimeError):
    """Base class for browser-control failures."""


class NotConnectedError(BrowserError):
    """Raised when an action is attempted before connecting to a browser."""


class LocateError(BrowserError):
    """Raised when an element cannot be targeted or is not found."""


class NavigationError(BrowserError):
    """Raised when navigation fails."""
