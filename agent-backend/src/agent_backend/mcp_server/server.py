"""FastMCP server exposing browser-control tools.

Thin adapter over :class:`BrowserController`. Runs as a stdio MCP server so it is
reusable both by the Agent SDK runner and the ``claude`` CLI. Browser failures are
returned to the model as ``Error: ...`` strings so the agent loop can recover
rather than aborting the tool call.
"""

from __future__ import annotations

import functools
import os
import tempfile
from collections.abc import Awaitable, Callable

from mcp.server.fastmcp import FastMCP

from ..browser.controller import BrowserController
from ..browser.errors import BrowserError
from ..config import BrowserConfig

_INSTRUCTIONS = (
    "Drive a single Chromium tab for autonomous page actions. Call browser_snapshot "
    "to read the page's accessibility tree, then act by ARIA role+name, CSS selector, "
    "or visible text. Re-snapshot after acting to verify."
)


def _safe[T](fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T | str]]:
    """Convert browser errors into recoverable tool-result strings."""

    @functools.wraps(fn)
    async def wrapper(*args: object, **kwargs: object) -> T | str:
        try:
            return await fn(*args, **kwargs)
        except BrowserError as exc:
            return f"Error: {exc}"

    return wrapper


def build_server(controller: BrowserController | None = None) -> FastMCP:
    """Build the MCP server. A controller may be injected for testing."""
    mcp = FastMCP("browser-control", instructions=_INSTRUCTIONS)
    state: dict[str, BrowserController | None] = {"controller": controller}

    async def ctrl() -> BrowserController:
        current = state["controller"]
        if current is None:
            current = BrowserController(BrowserConfig.from_env())
            state["controller"] = current
        await current.connect()
        return current

    @mcp.tool()
    @_safe
    async def browser_navigate(url: str) -> str:
        """Navigate the active tab to a URL."""
        return await (await ctrl()).navigate(url)

    @mcp.tool()
    @_safe
    async def browser_snapshot() -> str:
        """Return the page's accessibility tree (roles + names) to decide what to act on."""
        return await (await ctrl()).snapshot()

    @mcp.tool()
    @_safe
    async def browser_click(
        selector: str | None = None,
        role: str | None = None,
        name: str | None = None,
        text: str | None = None,
    ) -> str:
        """Click an element located by CSS selector, ARIA role (+ name), or visible text."""
        return await (await ctrl()).click(selector=selector, role=role, name=name, text=text)

    @mcp.tool()
    @_safe
    async def browser_type(
        value: str,
        selector: str | None = None,
        role: str | None = None,
        name: str | None = None,
        submit: bool = False,
    ) -> str:
        """Type into an input by CSS selector or ARIA role (+name). submit=true presses Enter."""
        return await (await ctrl()).type_text(
            value, selector=selector, role=role, name=name, submit=submit
        )

    @mcp.tool()
    @_safe
    async def browser_read(
        selector: str | None = None,
        role: str | None = None,
        name: str | None = None,
        text: str | None = None,
    ) -> str:
        """Read the visible text of the page, or of a specific element if a target is given."""
        return await (await ctrl()).read_text(selector=selector, role=role, name=name, text=text)

    @mcp.tool()
    @_safe
    async def browser_wait_for(selector: str | None = None, text: str | None = None) -> str:
        """Wait until an element (CSS selector) or visible text appears."""
        return await (await ctrl()).wait_for(selector=selector, text=text)

    @mcp.tool()
    @_safe
    async def browser_back() -> str:
        """Navigate back in the tab's history."""
        return await (await ctrl()).back()

    @mcp.tool()
    @_safe
    async def browser_screenshot() -> str:
        """Capture a PNG screenshot of the current view; returns the saved file path."""
        fd, path = tempfile.mkstemp(prefix="agent-shot-", suffix=".png")
        os.close(fd)
        return await (await ctrl()).screenshot(path)

    return mcp
