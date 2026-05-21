"""Shared pytest fixtures.

``browser_page`` yields a Playwright-managed headless Chromium page. The
controller's page actions are exercised against this real browser; the
``connect_over_cdp`` glue is covered by a dedicated error-path test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio


@pytest_asyncio.fixture
async def browser_page() -> AsyncIterator[object]:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            yield await browser.new_page()
        finally:
            await browser.close()
