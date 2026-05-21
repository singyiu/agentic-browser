#!/usr/bin/env python3
"""End-to-end check that a `block` verdict actually replaces the browser tab.

Drives the running built Chromium (CDP :9222) with the parental-control extension via
Playwright. Uses verdicts already in data/guardian_cache.db, so it is deterministic and
makes no LLM calls:

  * BLOCK : navigate to a URL cached as `block`  -> tab must become chrome-extension/block.html
  * ALLOW : navigate to a URL cached as `allow`  -> tab must stay on the page (no false block)

Prereqs: scripts/launch-guardian.sh and scripts/launch-chromium.sh already running.
Exit code 0 = all checks passed.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import time
from pathlib import Path

CDP_URL = "http://127.0.0.1:9222"
CACHE_DB = Path(__file__).resolve().parent.parent / "data" / "guardian_cache.db"
EVENT_LOG = Path(__file__).resolve().parent.parent / "data" / "guardian_events.jsonl"

BLOCK_URL = "https://www.youtube.com/watch?v=HTLPULt0eJ4"
ALLOW_URL = "https://www.youtube.com/results?search_query=scary+movie+trailer"


def cache_rows() -> list[tuple]:
    con = sqlite3.connect(f"file:{CACHE_DB}?mode=ro", uri=True, timeout=5)
    try:
        return con.execute("SELECT url_key, verdict, round(confidence,2) FROM verdicts").fetchall()
    finally:
        con.close()


def is_block_page(url: str) -> bool:
    return url.startswith("chrome-extension://") and "block.html" in url


async def wait_for(page, predicate, timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate(page.url):
            return True
        await asyncio.sleep(0.4)
    return predicate(page.url)


async def safe_goto(page, url: str) -> None:
    # The extension may redirect the tab mid-load; that aborts goto, which is expected.
    try:
        await page.goto(url, wait_until="commit", timeout=20000)
    except Exception as exc:  # noqa: BLE001
        print(f"  (goto interrupted, expected if blocked: {type(exc).__name__})")


async def main() -> int:
    from playwright.async_api import async_playwright

    print("=== cached verdicts (deterministic inputs) ===")
    rows = cache_rows()
    for r in rows:
        print("  ", r)
    have_allow = any(k == ALLOW_URL and v == "allow" for k, v, _ in rows)
    have_block = any(k == "youtube:HTLPULt0eJ4" and v == "block" for k, v, _ in rows)
    if not have_block:
        print("FAIL: expected youtube:HTLPULt0eJ4 cached as block — run a classification first.")
        return 1

    sw_logs: list[str] = []
    results: dict[str, tuple[str, str]] = {}
    block_reason = ""

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()

        def attach_sw(sw) -> None:
            try:
                sw.on("console", lambda m: sw_logs.append(m.text))
            except Exception:  # noqa: BLE001
                pass

        for sw in ctx.service_workers:
            attach_sw(sw)
        ctx.on("serviceworker", attach_sw)

        # --- BLOCK test ---
        print(f"\n=== BLOCK test: {BLOCK_URL} ===")
        page = await ctx.new_page()
        await safe_goto(page, BLOCK_URL)
        blocked = await wait_for(page, is_block_page, timeout_s=25)
        results["block"] = ("PASS" if blocked else "FAIL", page.url)
        if blocked:
            try:
                block_reason = await page.locator("#reason").inner_text(timeout=3000)
            except Exception:  # noqa: BLE001
                block_reason = "(reason element not found)"
        await page.close()

        # --- ALLOW test (negative) ---
        if have_allow:
            print(f"\n=== ALLOW test: {ALLOW_URL} ===")
            page2 = await ctx.new_page()
            await safe_goto(page2, ALLOW_URL)
            await asyncio.sleep(8)  # give the extension time to (wrongly) block, if it would
            stayed = not is_block_page(page2.url)
            results["allow"] = ("PASS" if stayed else "FAIL", page2.url)
            await page2.close()
        else:
            print("\n(skipping ALLOW test — no cached allow URL available)")

    print("\n=== RESULTS ===")
    for name, (status, url) in results.items():
        print(f"  {name:6} {status}   final url: {url[:90]}")
    if block_reason:
        print(f"  block page reason: {block_reason[:140]}")
    if sw_logs:
        print("\n  service-worker console (tail):")
        for line in sw_logs[-25:]:
            print("    ", line)

    ok = all(status == "PASS" for status, _ in results.values())
    print("\nOVERALL:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
