"""Starlette HTTP service exposing POST /classify and GET /health."""

from __future__ import annotations

import asyncio
import functools
import hmac
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from .access_requests import RequestStore
from .cache import VerdictCache
from .classifier import Classifier
from .config import GuardianConfig
from .event_log import EventLog
from .metrics import GuardianMetrics
from .normalize import extract_host, normalize_url
from .verdict import Verdict
from .whitelist import WhitelistStore, classify_entry


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


def create_app(
    config: GuardianConfig | None = None,
    *,
    classifier: Classifier | None = None,
    cache: VerdictCache | None = None,
    event_log: EventLog | None = None,
    metrics: GuardianMetrics | None = None,
    whitelist: WhitelistStore | None = None,
    request_store: RequestStore | None = None,
) -> Starlette:
    """Build the guardian app. Dependencies may be injected for testing."""
    config = config or GuardianConfig.from_env()
    classifier = classifier or Classifier(config)
    cache = cache or VerdictCache(config.cache_path)
    event_log = event_log or EventLog(config.event_log_path)
    metrics = metrics or GuardianMetrics()
    whitelist = whitelist or WhitelistStore(config.whitelist_path)
    request_store = request_store or RequestStore(config.requests_path)

    def _require_pin(request: Request) -> JSONResponse | None:
        """Gate parent-only endpoints behind GUARDIAN_PARENT_PIN (never sent to the extension)."""
        if not config.parent_pin:
            return JSONResponse({"error": "parent PIN not configured"}, status_code=503)
        # Constant-time compare: the PIN is short and the kid's machine can reach this endpoint.
        if not hmac.compare_digest(
            request.headers.get("X-Guardian-Parent-Pin", ""), config.parent_pin
        ):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return None

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def classify(request: Request) -> JSONResponse:
        if request.headers.get("X-Guardian-Token") != config.token:
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

        # Hot-reload the whitelist; a change invalidates cached verdicts.
        if await loop.run_in_executor(None, whitelist.reload_if_changed):
            await loop.run_in_executor(None, cache.clear)
        active = whitelist.current()

        # Hard URL allow: authoritative, checked before the cache, classifier skipped.
        if active.matches_url(url):
            event_log.log("whitelist_allow", url=url, url_key=url_key)
            metrics.record_whitelist_hit(host)
            return JSONResponse(_response("allow", "whitelisted", 1.0, [], url_key, False, 0))

        cached = await loop.run_in_executor(None, cache.get, url_key)
        if cached is not None:
            event_log.log("cache_hit", url=url, url_key=url_key, verdict=cached.verdict)
            metrics.record_cache_hit(host)
            return JSONResponse(
                _response(cached.verdict, cached.reason, cached.confidence, [], url_key, True, 0)
            )

        try:
            verdict: Verdict = await asyncio.wait_for(
                classifier.classify(
                    body, screenshot_b64=screenshot, approved_topics=active.content_entries
                ),
                timeout=config.classify_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 - fail-open on timeout or any error
            event_log.log("fail_open", url=url, url_key=url_key, reason=type(exc).__name__)
            metrics.record_fail_open(host)
            return JSONResponse(
                _response(
                    "allow", "classification_unavailable", 0.0, [], url_key, False, _ms(start)
                )
            )

        # Escalate to a screenshot when the text verdict is inconclusive (first call only).
        low_confidence = verdict.confidence < config.screenshot_confidence_threshold
        if (
            verdict.verdict != "block"
            and (verdict.verdict == "need_screenshot" or low_confidence)
            and can_escalate
            and not screenshot
        ):
            event_log.log("escalate", url=url, url_key=url_key, confidence=verdict.confidence)
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
            None, cache.put, url_key, final, verdict.reason, verdict.confidence
        )
        event_log.log(
            final,
            url=url,
            url_key=url_key,
            verdict=final,
            reason=verdict.reason,
            confidence=verdict.confidence,
            categories=list(verdict.categories),
            had_screenshot=bool(screenshot),
            duration_ms=duration,
        )
        metrics.record_classification(final, verdict.categories, duration, host)
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

    async def dwell(request: Request) -> JSONResponse:
        if request.headers.get("X-Guardian-Token") != config.token:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        url_key = str(body.get("url_key", "")).strip()
        dwell_ms = body.get("dwell_ms")
        if (
            not url_key
            or isinstance(dwell_ms, bool)
            or not isinstance(dwell_ms, (int, float))
            or dwell_ms < 0
        ):
            return JSONResponse(
                {"error": "url_key and non-negative dwell_ms required"}, status_code=422
            )
        host = extract_host(url_key)
        metrics.record_dwell(host, float(dwell_ms) / 1000.0)
        event_log.log("dwell", url_key=url_key, host=host, dwell_ms=int(dwell_ms))
        return JSONResponse({"ok": True})

    async def whitelist_endpoint(request: Request) -> JSONResponse:
        if request.headers.get("X-Guardian-Token") != config.token:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if request.method == "GET":
            entries = [
                {"value": value, "type": classify_entry(value)}
                for value in whitelist.current().values
            ]
            return JSONResponse({"entries": entries})
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        entry = str(body.get("entry", "")).strip()
        if not entry or len(entry) > 512 or not entry.isprintable():
            return JSONResponse(
                {"error": "entry must be a non-empty, single-line string (max 512 chars)"},
                status_code=422,
            )
        loop = asyncio.get_running_loop()
        try:
            if request.method == "POST":
                await loop.run_in_executor(None, whitelist.add, entry)
            else:  # DELETE
                await loop.run_in_executor(None, whitelist.remove, entry)
            await loop.run_in_executor(None, cache.clear)  # a whitelist change invalidates verdicts
        except OSError:
            return JSONResponse({"error": "whitelist write failed"}, status_code=500)
        event_log.log(f"whitelist_{request.method.lower()}", entry=entry)
        if request.method == "POST":
            return JSONResponse({"value": entry, "type": classify_entry(entry)})
        return JSONResponse({"ok": True})

    async def access_request_endpoint(request: Request) -> JSONResponse:
        # Teen-facing: the extension already holds X-Guardian-Token, so submitting/checking a
        # request is low-privilege. Approving is NOT here — that needs the parent PIN (below).
        if request.headers.get("X-Guardian-Token") != config.token:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        loop = asyncio.get_running_loop()

        if request.method == "GET":
            url = request.query_params.get("url", "").strip()
            if not url:
                return JSONResponse({"error": "url query param required"}, status_code=422)
            match = request_store.current().latest_for_url_key(normalize_url(url))
            if match is None:
                return JSONResponse({"status": "none"})
            return JSONResponse(
                {
                    "status": match.status,
                    "id": match.id,
                    "decision_note": match.decision_note,
                    "whitelist_entry": match.whitelist_entry,
                }
            )

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        url = str(body.get("url", "")).strip()
        note = str(body.get("note", "")).strip()
        reason = str(body.get("reason", "")).strip()
        if not url or len(url) > 2048 or not url.startswith(("http://", "https://")):
            return JSONResponse(
                {"error": "url must be an http(s) URL (max 2048 chars)"}, status_code=422
            )
        if len(note) > 500 or len(reason) > 500:
            return JSONResponse(
                {"error": "note and reason must be at most 500 chars"}, status_code=422
            )
        url_key = normalize_url(url)
        host = extract_host(url_key)
        req = await loop.run_in_executor(
            None,
            functools.partial(
                request_store.add_request,
                url=url,
                url_key=url_key,
                host=host,
                reason=reason,
                note=note,
            ),
        )
        event_log.log("access_request", url=url, url_key=url_key, host=host, id=req.id)
        metrics.record_access_request(host)
        return JSONResponse({"id": req.id, "status": req.status})

    async def review_page(_request: Request) -> FileResponse:
        # Inert HTML shell (no secrets); the data + actions below require the PIN.
        return FileResponse(Path(__file__).parent / "review.html", media_type="text/html")

    async def review_requests(request: Request) -> JSONResponse:
        guard = _require_pin(request)
        if guard is not None:
            return guard
        snapshot = request_store.current()
        return JSONResponse(
            {
                "pending": [asdict(r) for r in snapshot.pending()],
                "recent": [asdict(r) for r in snapshot.recent_decided()],
            }
        )

    async def review_decision(request: Request) -> JSONResponse:
        guard = _require_pin(request)
        if guard is not None:
            return guard
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        request_id = str(body.get("id", "")).strip()
        decision = str(body.get("decision", "")).strip()
        if not request_id or decision not in ("approve", "reject"):
            return JSONResponse(
                {"error": "id and decision (approve|reject) required"}, status_code=422
            )
        note = str(body.get("note", "")).strip() or None
        loop = asyncio.get_running_loop()

        # The whitelist entry is the parent's choice; default to the request's RAW url (an exact
        # match) — never the url_key, which collapses YouTube videos to a non-matching topic.
        entry = str(body.get("whitelist_entry", "")).strip()
        if decision == "approve" and not entry:
            # Default to the request's RAW url (always a valid http(s) URL; empty only for an
            # unknown id, which falls through to decide() -> 404 below).
            existing = request_store.current().by_id(request_id)
            entry = existing.url if existing is not None else ""
        # The entry is added to the whitelist (and content entries are injected verbatim into the
        # classifier prompt), so apply the same guard as POST /whitelist: single line, bounded.
        if entry and (len(entry) > 512 or not entry.isprintable()):
            return JSONResponse(
                {"error": "whitelist entry must be a single-line string (max 512 chars)"},
                status_code=422,
            )

        try:
            decided = await loop.run_in_executor(
                None,
                functools.partial(
                    request_store.decide,
                    request_id,
                    decision=decision,
                    whitelist_entry=entry if decision == "approve" else None,
                    decision_note=note,
                ),
            )
        except KeyError:
            return JSONResponse({"error": "request not found"}, status_code=404)
        except ValueError:
            return JSONResponse({"error": "request already decided"}, status_code=422)

        if decision == "approve":
            try:
                await loop.run_in_executor(None, whitelist.add, entry)
                await loop.run_in_executor(None, cache.clear)  # an allow invalidates a cached block
            except OSError:
                return JSONResponse({"error": "whitelist write failed"}, status_code=500)
            event_log.log("access_request_approved", id=request_id, entry=entry)
            metrics.record_access_decision("approve")
        else:
            event_log.log("access_request_rejected", id=request_id)
            metrics.record_access_decision("reject")
        return JSONResponse({"id": decided.id, "status": decided.status})

    app = Starlette(
        routes=[
            Route("/health", health),
            Route("/classify", classify, methods=["POST"]),
            Route("/dwell", dwell, methods=["POST"]),
            Route("/whitelist", whitelist_endpoint, methods=["GET", "POST", "DELETE"]),
            Route("/access-request", access_request_endpoint, methods=["GET", "POST"]),
            Route("/review", review_page, methods=["GET"]),
            Route("/review/requests", review_requests, methods=["GET"]),
            Route("/review/decision", review_decision, methods=["POST"]),
        ]
    )
    app.state.config = config
    return app
