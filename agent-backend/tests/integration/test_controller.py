"""Integration tests: drive a real headless Chromium via the controller."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from agent_backend.browser.controller import BrowserController
from agent_backend.browser.errors import BrowserError, LocateError
from agent_backend.config import BrowserConfig

pytestmark = pytest.mark.integration

FIXTURE_URI = (Path(__file__).parent / "fixtures" / "page.html").resolve().as_uri()


@pytest_asyncio.fixture
async def controller(browser_page: object) -> AsyncIterator[BrowserController]:
    yield BrowserController(page=browser_page)  # type: ignore[arg-type]


async def test_navigate_and_snapshot(controller: BrowserController) -> None:
    msg = await controller.navigate(FIXTURE_URI)
    assert "Navigated" in msg
    snapshot = await controller.snapshot()
    assert "Demo Heading" in snapshot


async def test_read_body_text(controller: BrowserController) -> None:
    await controller.navigate(FIXTURE_URI)
    body = await controller.read_text()
    assert "Hello from the fixture page" in body


async def test_type_click_read_roundtrip(controller: BrowserController) -> None:
    await controller.navigate(FIXTURE_URI)
    await controller.type_text("widget", selector="#q")
    await controller.click(selector="#go")
    out = await controller.read_text(selector="#out")
    assert out == "clicked:widget"


async def test_type_with_submit(controller: BrowserController) -> None:
    await controller.navigate(FIXTURE_URI)
    msg = await controller.type_text("hi", selector="#q", submit=True)
    assert "submitted" in msg


async def test_click_by_role_and_name(controller: BrowserController) -> None:
    await controller.navigate(FIXTURE_URI)
    await controller.click(role="link", name="More info")
    assert controller.page.url.endswith("#more")


async def test_wait_for_selector_and_text(controller: BrowserController) -> None:
    await controller.navigate(FIXTURE_URI)
    assert "appeared" in await controller.wait_for(selector="#go")
    assert "appeared" in await controller.wait_for(text="Demo Heading")


async def test_wait_for_requires_a_target(controller: BrowserController) -> None:
    await controller.navigate(FIXTURE_URI)
    with pytest.raises(LocateError):
        await controller.wait_for()


async def test_back_navigation(controller: BrowserController) -> None:
    await controller.navigate(FIXTURE_URI)
    await controller.navigate("about:blank")
    await controller.back()
    assert "page.html" in controller.page.url


async def test_click_missing_element_raises(controller: BrowserController) -> None:
    await controller.navigate(FIXTURE_URI)
    with pytest.raises(LocateError):
        await controller.click(selector="#does-not-exist", timeout_ms=500)


async def test_screenshot_writes_file(controller: BrowserController, tmp_path: Path) -> None:
    await controller.navigate(FIXTURE_URI)
    path = str(tmp_path / "shot.png")
    returned = await controller.screenshot(path)
    assert returned == path
    assert Path(path).stat().st_size > 0


async def test_connect_failure_raises_browser_error() -> None:
    ctrl = BrowserController(BrowserConfig(cdp_url="http://127.0.0.1:1"))
    with pytest.raises(BrowserError):
        await ctrl.connect()
