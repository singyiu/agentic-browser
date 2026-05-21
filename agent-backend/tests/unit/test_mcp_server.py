"""Unit tests for the MCP server adapter (with a fake controller)."""

from __future__ import annotations

from agent_backend.browser.errors import NavigationError
from agent_backend.mcp_server.server import build_server

EXPECTED_TOOLS = {
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_read",
    "browser_wait_for",
    "browser_back",
    "browser_screenshot",
}


class FakeController:
    def __init__(self) -> None:
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def navigate(self, url: str) -> str:
        return f"NAV:{url}"

    async def snapshot(self) -> str:
        return "SNAP"

    async def click(self, **kwargs: object) -> str:
        return f"CLICK:{kwargs}"

    async def type_text(self, value: str, **kwargs: object) -> str:
        return f"TYPE:{value}"

    async def read_text(self, **kwargs: object) -> str:
        return "TEXT"

    async def wait_for(self, **kwargs: object) -> str:
        return "WAIT"

    async def back(self) -> str:
        return "BACK"

    async def screenshot(self, path: str) -> str:
        return path


async def test_all_tools_registered() -> None:
    server = build_server(controller=FakeController())
    names = {tool.name for tool in await server.list_tools()}
    assert EXPECTED_TOOLS <= names


async def test_navigate_routes_to_controller() -> None:
    server = build_server(controller=FakeController())
    result = await server.call_tool("browser_navigate", {"url": "http://example.com"})
    assert "NAV:http://example.com" in str(result)


async def test_remaining_tools_route_to_controller() -> None:
    server = build_server(controller=FakeController())
    assert "SNAP" in str(await server.call_tool("browser_snapshot", {}))
    assert "CLICK" in str(await server.call_tool("browser_click", {"selector": "#x"}))
    assert "TYPE:hello" in str(await server.call_tool("browser_type", {"value": "hello"}))
    assert "TEXT" in str(await server.call_tool("browser_read", {}))
    assert "WAIT" in str(await server.call_tool("browser_wait_for", {"selector": "#x"}))
    assert "BACK" in str(await server.call_tool("browser_back", {}))


async def test_screenshot_tool_returns_png_path() -> None:
    server = build_server(controller=FakeController())
    result = await server.call_tool("browser_screenshot", {})
    assert ".png" in str(result)


async def test_lazy_connect_invoked() -> None:
    fake = FakeController()
    server = build_server(controller=fake)
    await server.call_tool("browser_snapshot", {})
    assert fake.connected is True


async def test_browser_error_becomes_recoverable_string() -> None:
    class Boom(FakeController):
        async def navigate(self, url: str) -> str:
            raise NavigationError("boom")

    server = build_server(controller=Boom())
    result = await server.call_tool("browser_navigate", {"url": "x"})
    assert "Error: boom" in str(result)
