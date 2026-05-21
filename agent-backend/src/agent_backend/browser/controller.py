"""Async Playwright wrapper that drives a Chromium instance over CDP.

This is the real browser-control logic. The MCP server is a thin adapter over it.
It connects to an *already running* Chromium (launched with
``--remote-debugging-port``) via ``connect_over_cdp`` — it never launches the
user's browser itself.
"""

from __future__ import annotations

from playwright.async_api import Browser, Locator, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from ..config import BrowserConfig
from .errors import BrowserError, LocateError, NavigationError, NotConnectedError
from .locate import Target, resolve_target
from .snapshot import DEFAULT_MAX_CHARS, truncate_snapshot

_DEFAULT_TIMEOUT_MS = 8_000


class BrowserController:
    """Owns the Playwright connection and exposes high-level page actions."""

    def __init__(self, config: BrowserConfig | None = None, *, page: Page | None = None) -> None:
        if config is None and page is None:
            raise ValueError("BrowserController requires either a config or an injected page")
        self._config = config
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = page
        self._owns_connection = page is None

    @property
    def page(self) -> Page:
        if self._page is None:
            raise NotConnectedError("Not connected to a browser. Call connect() first.")
        return self._page

    async def connect(self) -> None:
        """Connect to the running Chromium and select the active tab (idempotent)."""
        if self._page is not None:
            return
        assert self._config is not None  # guaranteed by __init__
        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.connect_over_cdp(self._config.cdp_url)
        except PlaywrightError as exc:
            await self.close()
            raise BrowserError(
                f"Could not connect to Chromium CDP at {self._config.cdp_url}. "
                "Is Chromium running with --remote-debugging-port? "
                f"({exc})"
            ) from exc
        self._page = await self._select_page()

    async def _select_page(self) -> Page:
        assert self._browser is not None
        context = (
            self._browser.contexts[0]
            if self._browser.contexts
            else (await self._browser.new_context())
        )
        return context.pages[0] if context.pages else await context.new_page()

    def _locator(self, target: Target) -> Locator:
        page = self.page
        if target.strategy == "selector":
            return page.locator(target.value)
        if target.strategy == "role":
            if target.name:
                return page.get_by_role(target.value, name=target.name)  # type: ignore[arg-type]
            return page.get_by_role(target.value)  # type: ignore[arg-type]
        return page.get_by_text(target.value)

    async def navigate(self, url: str) -> str:
        page = self.page
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=_DEFAULT_TIMEOUT_MS * 2)
        except PlaywrightError as exc:
            raise NavigationError(f"Failed to navigate to {url}: {exc}") from exc
        return f"Navigated to {page.url} (title: {await page.title()!r})"

    async def snapshot(self, max_chars: int = DEFAULT_MAX_CHARS) -> str:
        page = self.page
        tree = await page.locator("body").aria_snapshot()
        return truncate_snapshot(f"# page: {page.url}\n{tree}", max_chars)

    async def click(
        self,
        *,
        selector: str | None = None,
        role: str | None = None,
        name: str | None = None,
        text: str | None = None,
        timeout_ms: int = _DEFAULT_TIMEOUT_MS,
    ) -> str:
        target = resolve_target(selector=selector, role=role, name=name, text=text)
        try:
            await self._locator(target).first.click(timeout=timeout_ms)
        except PlaywrightError as exc:
            raise LocateError(f"Could not click {target.strategy}={target.value!r}: {exc}") from exc
        return f"Clicked {target.strategy}={target.value!r}"

    async def type_text(
        self,
        value: str,
        *,
        selector: str | None = None,
        role: str | None = None,
        name: str | None = None,
        submit: bool = False,
        timeout_ms: int = _DEFAULT_TIMEOUT_MS,
    ) -> str:
        target = resolve_target(selector=selector, role=role, name=name)
        locator = self._locator(target).first
        try:
            await locator.fill(value, timeout=timeout_ms)
            if submit:
                await locator.press("Enter")
        except PlaywrightError as exc:
            raise LocateError(
                f"Could not type into {target.strategy}={target.value!r}: {exc}"
            ) from exc
        suffix = " and submitted" if submit else ""
        return f"Typed {value!r} into {target.strategy}={target.value!r}{suffix}"

    async def read_text(
        self,
        *,
        selector: str | None = None,
        role: str | None = None,
        name: str | None = None,
        text: str | None = None,
        max_chars: int = DEFAULT_MAX_CHARS,
        timeout_ms: int = _DEFAULT_TIMEOUT_MS,
    ) -> str:
        page = self.page
        if not any((selector, role, text)):
            locator = page.locator("body")
        else:
            locator = self._locator(
                resolve_target(selector=selector, role=role, name=name, text=text)
            ).first
        try:
            content = await locator.inner_text(timeout=timeout_ms)
        except PlaywrightError as exc:
            raise LocateError(f"Could not read text: {exc}") from exc
        return truncate_snapshot(content, max_chars)

    async def wait_for(
        self,
        *,
        selector: str | None = None,
        text: str | None = None,
        timeout_ms: int = _DEFAULT_TIMEOUT_MS,
    ) -> str:
        page = self.page
        try:
            if selector:
                await page.wait_for_selector(selector, timeout=timeout_ms)
                return f"Element {selector!r} appeared"
            if text:
                await page.get_by_text(text).first.wait_for(timeout=timeout_ms)
                return f"Text {text!r} appeared"
        except PlaywrightError as exc:
            raise LocateError(f"Timed out waiting: {exc}") from exc
        raise LocateError("wait_for requires either selector or text")

    async def back(self) -> str:
        page = self.page
        try:
            await page.go_back(wait_until="domcontentloaded")
        except PlaywrightError as exc:
            raise NavigationError(f"Could not go back: {exc}") from exc
        return f"Went back to {page.url}"

    async def screenshot(self, path: str) -> str:
        page = self.page
        try:
            await page.screenshot(path=path, full_page=False)
        except PlaywrightError as exc:
            raise BrowserError(f"Could not capture screenshot: {exc}") from exc
        return path

    async def close(self) -> None:
        """Tear down the Playwright connection (does not close the user's browser)."""
        if not self._owns_connection:
            self._page = None
            return
        if self._browser is not None:
            try:
                await self._browser.close()
            except PlaywrightError:
                pass
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
        self._page = None
