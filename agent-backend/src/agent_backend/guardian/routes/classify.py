"""Routes: POST /classify and POST /search-classify.

Extracted from service.py.  Both handlers are token-gated (kid extension uses
``X-Guardian-Token``).  Helpers ``_ms`` and ``_response`` live here because they
are only needed by these two endpoints.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..normalize import extract_host, normalize_url
from ..search_classifier import classify_search_query
from .deps import GuardianDeps, make_auth_helpers

_MAX_QUERY_CHARS = 500


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _response(
    verdict: str,
    reason: str,
    confidence: float,
    categories: list[str],
    url_key: str,
    cached: bool,
    duration_ms: int,
) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "reason": reason,
        "confidence": confidence,
        "categories_matched": categories,
        "url_key": url_key,
        "cached": cached,
        "duration_ms": duration_ms,
    }


def build_routes(deps: GuardianDeps) -> list[Route]:
    """Return [/classify, /search-classify] routes bound to *deps*."""
    resolve_runtime, _ = make_auth_helpers(deps)

    async def classify(request: Request) -> JSONResponse:
        rt = resolve_runtime(request)
        if rt is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)

        url = str(body.get("url", ""))
        url_key = str(body.get("url_key") or normalize_url(url))
        host = extract_host(url_key)
        can_escalate = bool(body.get("can_escalate", True))
        screenshot = body.get("screenshot_b64")
        start = time.monotonic()
        loop = asyncio.get_running_loop()

        # Hot-reload this teen's lists + prompt and the shared Global lists + prompt; any change
        # invalidates the teen's cached verdicts (Global edits are picked up here lazily, per teen).
        gl = deps.pm.global_runtime()
        changed = False
        for reloader in (
            rt.whitelist.reload_if_changed,
            rt.blocklist.reload_if_changed,
            rt.prompt_store.reload_if_changed,
            rt.search_allow.reload_if_changed,
            rt.search_block.reload_if_changed,
            gl.whitelist.reload_if_changed,
            gl.blocklist.reload_if_changed,
            gl.prompt_store.reload_if_changed,
            gl.search_allow.reload_if_changed,
            gl.search_block.reload_if_changed,
        ):
            changed = await loop.run_in_executor(None, reloader) or changed
        if changed:
            await loop.run_in_executor(None, rt.cache.clear)

        wl, bl = rt.whitelist.current(), rt.blocklist.current()
        gwl, gbl = gl.whitelist.current(), gl.blocklist.current()

        # Hard URL rules: authoritative, checked before the cache (classifier skipped).
        # Individual always wins: kid block, then kid allow, then Global block, Global allow.
        if bl.matches_url(url):
            deps.event_log.log("blocklist_block", url=url, url_key=url_key, profile=rt.name)
            deps.metrics.record_classification("block", (), 0, host)
            return JSONResponse(_response("block", "blocklisted", 1.0, [], url_key, False, 0))
        if wl.matches_url(url):
            deps.event_log.log("whitelist_allow", url=url, url_key=url_key, profile=rt.name)
            deps.metrics.record_whitelist_hit(host)
            return JSONResponse(_response("allow", "whitelisted", 1.0, [], url_key, False, 0))
        if gbl.matches_url(url):
            deps.event_log.log(
                "blocklist_block", url=url, url_key=url_key, profile=rt.name, scope="global"
            )
            deps.metrics.record_classification("block", (), 0, host)
            return JSONResponse(
                _response("block", "blocklisted_global", 1.0, [], url_key, False, 0)
            )
        if gwl.matches_url(url):
            deps.event_log.log(
                "whitelist_allow", url=url, url_key=url_key, profile=rt.name, scope="global"
            )
            deps.metrics.record_whitelist_hit(host)
            return JSONResponse(
                _response("allow", "whitelisted_global", 1.0, [], url_key, False, 0)
            )

        cached = await loop.run_in_executor(None, rt.cache.get, url_key)
        if cached is not None:
            deps.event_log.log(
                "cache_hit", url=url, url_key=url_key, verdict=cached.verdict, profile=rt.name
            )
            deps.metrics.record_cache_hit(host)
            return JSONResponse(
                _response(cached.verdict, cached.reason, cached.confidence, [], url_key, True, 0)
            )

        try:
            from ..verdict import Verdict  # noqa: F401 (type annotation only)

            verdict: Verdict = await asyncio.wait_for(
                deps.classifier.classify(
                    body,
                    screenshot_b64=screenshot,
                    age=rt.age,
                    policy=deps.pm.merged_policy(rt),
                    approved_topics=(*wl.content_entries, *gwl.content_entries),
                    disallowed_topics=(*bl.content_entries, *gbl.content_entries),
                ),
                timeout=deps.config.classify_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 - verdict on error is config.classify_fail_mode
            mode = deps.config.classify_fail_mode
            deps.event_log.log(
                f"fail_{mode}",
                url=url,
                url_key=url_key,
                reason=type(exc).__name__,
                profile=rt.name,
            )
            deps.metrics.record_fail_open(host)
            failure_verdict = "block" if mode == "closed" else "allow"
            return JSONResponse(
                _response(
                    failure_verdict,
                    "classification_unavailable",
                    0.0,
                    [],
                    url_key,
                    False,
                    _ms(start),
                )
            )

        # Escalate to a screenshot when the text verdict is inconclusive (first call only).
        low_confidence = verdict.confidence < deps.config.screenshot_confidence_threshold
        if (
            verdict.verdict != "block"
            and (verdict.verdict == "need_screenshot" or low_confidence)
            and can_escalate
            and not screenshot
        ):
            deps.event_log.log(
                "escalate",
                url=url,
                url_key=url_key,
                confidence=verdict.confidence,
                profile=rt.name,
            )
            return JSONResponse(
                _response(
                    "need_screenshot",
                    verdict.reason,
                    verdict.confidence,
                    list(verdict.categories),
                    url_key,
                    False,
                    _ms(start),
                )
            )

        # Resolve to a concrete allow/block (need_screenshot with no further escalation => allow).
        final = verdict.verdict if verdict.verdict == "block" else "allow"
        duration = _ms(start)
        await loop.run_in_executor(
            None, rt.cache.put, url_key, final, verdict.reason, verdict.confidence
        )
        deps.event_log.log(
            final,
            url=url,
            url_key=url_key,
            verdict=final,
            reason=verdict.reason,
            confidence=verdict.confidence,
            categories=list(verdict.categories),
            had_screenshot=bool(screenshot),
            duration_ms=duration,
            profile=rt.name,
        )
        deps.metrics.record_classification(final, verdict.categories, duration, host)
        return JSONResponse(
            _response(
                final,
                verdict.reason,
                verdict.confidence,
                list(verdict.categories),
                url_key,
                False,
                duration,
            )
        )

    async def search_classify(request: Request) -> JSONResponse:
        """Classify a bare search query (token-authed): parent keyword lists, then age-aware AI.

        Mirrors classify(): parent lists are checked synchronously, the AI verdict is cached
        under a ``search:`` key, and any error/timeout fails open (allow) so a backend hiccup
        never blocks all searching.
        """
        rt = resolve_runtime(request)
        if rt is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        query = str(body.get("query", "")).strip()
        if not query or len(query) > _MAX_QUERY_CHARS:
            return JSONResponse(
                {"error": f"query required (1-{_MAX_QUERY_CHARS} chars)"}, status_code=422
            )
        loop = asyncio.get_running_loop()

        # Hot-reload this teen's + Global's search lists and prompt; any change clears the teen's
        # cached verdicts (search verdicts share the cache under a "search:" key prefix).
        gl = deps.pm.global_runtime()
        changed = False
        for reloader in (
            rt.search_allow.reload_if_changed,
            rt.search_block.reload_if_changed,
            rt.prompt_store.reload_if_changed,
            gl.search_allow.reload_if_changed,
            gl.search_block.reload_if_changed,
            gl.prompt_store.reload_if_changed,
        ):
            changed = await loop.run_in_executor(None, reloader) or changed
        if changed:
            await loop.run_in_executor(None, rt.cache.clear)

        cache_key = "search:" + query.lower()[:_MAX_QUERY_CHARS]
        cached = await loop.run_in_executor(None, rt.cache.get, cache_key)
        if cached is not None:
            return JSONResponse(
                {"verdict": cached.verdict, "reason": cached.reason, "cached": True}
            )

        try:
            verdict = await asyncio.wait_for(
                classify_search_query(
                    query,
                    teen_allow=rt.search_allow.current(),
                    global_allow=gl.search_allow.current(),
                    teen_block=rt.search_block.current(),
                    global_block=gl.search_block.current(),
                    classifier=deps.classifier,
                    age=rt.age,
                    policy=deps.pm.merged_policy(rt),
                ),
                timeout=deps.config.classify_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 - verdict on error is config.classify_fail_mode
            mode = deps.config.classify_fail_mode
            deps.event_log.log(f"search_fail_{mode}", reason=type(exc).__name__, profile=rt.name)
            failure_verdict = "block" if mode == "closed" else "allow"
            return JSONResponse(
                {"verdict": failure_verdict, "reason": "classification_unavailable"}
            )

        await loop.run_in_executor(
            None,
            rt.cache.put,
            cache_key,
            verdict.verdict,
            verdict.reason,
            verdict.confidence,
        )
        # Never log the raw query (it may be sensitive); record only its length + verdict.
        deps.event_log.log(
            "search_classify", query_len=len(query), verdict=verdict.verdict, profile=rt.name
        )
        return JSONResponse({"verdict": verdict.verdict, "reason": verdict.reason, "cached": False})

    return [
        Route("/classify", classify, methods=["POST"]),
        Route("/search-classify", search_classify, methods=["POST"]),
    ]
