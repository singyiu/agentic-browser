"""Starlette HTTP service exposing POST /classify and GET /health."""

from __future__ import annotations

import asyncio
import functools
import hmac
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from ..config import ConfigError
from .access_requests import RequestStore
from .cache import VerdictCache
from .classifier import Classifier
from .config import GuardianConfig
from .event_log import EventLog
from .metrics import GuardianMetrics
from .normalize import extract_host, normalize_url
from .profiles import DEFAULT_PROFILE_NAME, ProfileRegistry
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


@dataclass(frozen=True, slots=True)
class _ProfileRuntime:
    """One teen's isolated stores, resolved per request from the X-Guardian-Token.

    Holds the profile's secret ``token``; this is an internal object and must never be
    serialized into a response.
    """

    name: str
    token: str
    whitelist: WhitelistStore
    request_store: RequestStore
    cache: VerdictCache


def _build_runtimes(
    config: GuardianConfig,
    registry: ProfileRegistry | None,
    runtimes: dict[str, _ProfileRuntime] | None,
    *,
    cache: VerdictCache | None,
    whitelist: WhitelistStore | None,
    request_store: RequestStore | None,
) -> dict[str, _ProfileRuntime]:
    """Resolve per-profile stores. Precedence: injected runtimes > registry > single default.

    The single-default branch wraps the injected (or config-path) stores, so existing
    single-profile callers and tests are byte-identical.
    """
    if runtimes is not None:
        return runtimes
    if registry is not None:
        built: dict[str, _ProfileRuntime] = {}
        for profile in registry.all():
            # Each teen's stores live in their own dir; create it before the stores open.
            for path in (profile.whitelist_path, profile.requests_path, profile.cache_path):
                try:
                    Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    raise ConfigError(
                        f"Cannot create data directory for profile {profile.name!r}: {exc}"
                    ) from exc
            built[profile.name] = _ProfileRuntime(
                name=profile.name,
                token=profile.token,
                whitelist=WhitelistStore(profile.whitelist_path),
                request_store=RequestStore(profile.requests_path),
                cache=VerdictCache(profile.cache_path),
            )
        return built
    if not config.token:
        # Guard the public create_app() against a silent lockout: an empty default token
        # matches no request. (__main__ resolves the registry first, which also checks this.)
        raise ConfigError(
            "create_app: no registry/runtimes and GUARDIAN_TOKEN is empty — nothing could "
            "authenticate. Set GUARDIAN_TOKEN or pass a registry/runtimes."
        )
    default = _ProfileRuntime(
        name=DEFAULT_PROFILE_NAME,
        token=config.token,
        whitelist=whitelist or WhitelistStore(config.whitelist_path),
        request_store=request_store or RequestStore(config.requests_path),
        cache=cache or VerdictCache(config.cache_path),
    )
    return {default.name: default}


def create_app(
    config: GuardianConfig | None = None,
    *,
    classifier: Classifier | None = None,
    cache: VerdictCache | None = None,
    event_log: EventLog | None = None,
    metrics: GuardianMetrics | None = None,
    whitelist: WhitelistStore | None = None,
    request_store: RequestStore | None = None,
    registry: ProfileRegistry | None = None,
    runtimes: dict[str, _ProfileRuntime] | None = None,
) -> Starlette:
    """Build the guardian app. Dependencies may be injected for testing.

    One backend can serve several teen profiles: each request's ``X-Guardian-Token`` resolves
    to that teen's isolated whitelist, access-request store, and verdict cache. With no
    registry/runtimes a single ``"default"`` profile wraps the injected/config-path stores.
    """
    config = config or GuardianConfig.from_env()
    classifier = classifier or Classifier(config)
    event_log = event_log or EventLog(config.event_log_path)
    metrics = metrics or GuardianMetrics()
    profile_runtimes = _build_runtimes(
        config,
        registry,
        runtimes,
        cache=cache,
        whitelist=whitelist,
        request_store=request_store,
    )

    def _resolve_runtime(request: Request) -> _ProfileRuntime | None:
        """Map ``X-Guardian-Token`` to its teen profile, or None to reject.

        Compares against every profile's token with ``hmac.compare_digest`` (selecting after
        the loop, not on first hit) so a near-correct token can't be confirmed byte-by-byte by
        timing. An empty/absent token is rejected up front.
        """
        token = request.headers.get("X-Guardian-Token", "")
        if not token:
            return None
        match: _ProfileRuntime | None = None
        for runtime in profile_runtimes.values():
            if hmac.compare_digest(runtime.token, token):
                match = runtime
        return match

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
        rt = _resolve_runtime(request)
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

        # Hot-reload the whitelist; a change invalidates cached verdicts.
        if await loop.run_in_executor(None, rt.whitelist.reload_if_changed):
            await loop.run_in_executor(None, rt.cache.clear)
        active = rt.whitelist.current()

        # Hard URL allow: authoritative, checked before the cache, classifier skipped.
        if active.matches_url(url):
            event_log.log("whitelist_allow", url=url, url_key=url_key, profile=rt.name)
            metrics.record_whitelist_hit(host)
            return JSONResponse(_response("allow", "whitelisted", 1.0, [], url_key, False, 0))

        cached = await loop.run_in_executor(None, rt.cache.get, url_key)
        if cached is not None:
            event_log.log(
                "cache_hit", url=url, url_key=url_key, verdict=cached.verdict, profile=rt.name
            )
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
            event_log.log(
                "fail_open", url=url, url_key=url_key, reason=type(exc).__name__, profile=rt.name
            )
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
            event_log.log(
                "escalate", url=url, url_key=url_key, confidence=verdict.confidence, profile=rt.name
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
            profile=rt.name,
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
        rt = _resolve_runtime(request)
        if rt is None:
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
        event_log.log("dwell", url_key=url_key, host=host, dwell_ms=int(dwell_ms), profile=rt.name)
        return JSONResponse({"ok": True})

    async def whitelist_endpoint(request: Request) -> JSONResponse:
        rt = _resolve_runtime(request)
        if rt is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if request.method == "GET":
            entries = [
                {"value": value, "type": classify_entry(value)}
                for value in rt.whitelist.current().values
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
                await loop.run_in_executor(None, rt.whitelist.add, entry)
            else:  # DELETE
                await loop.run_in_executor(None, rt.whitelist.remove, entry)
            # A whitelist change invalidates cached verdicts.
            await loop.run_in_executor(None, rt.cache.clear)
        except OSError:
            return JSONResponse({"error": "whitelist write failed"}, status_code=500)
        event_log.log(f"whitelist_{request.method.lower()}", entry=entry, profile=rt.name)
        if request.method == "POST":
            return JSONResponse({"value": entry, "type": classify_entry(entry)})
        return JSONResponse({"ok": True})

    async def access_request_endpoint(request: Request) -> JSONResponse:
        # Teen-facing: the extension already holds X-Guardian-Token, so submitting/checking a
        # request is low-privilege. Approving is NOT here — that needs the parent PIN (below).
        rt = _resolve_runtime(request)
        if rt is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        loop = asyncio.get_running_loop()

        if request.method == "GET":
            url = request.query_params.get("url", "").strip()
            if not url:
                return JSONResponse({"error": "url query param required"}, status_code=422)
            match = rt.request_store.current().latest_for_url_key(normalize_url(url))
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
                rt.request_store.add_request,
                url=url,
                url_key=url_key,
                host=host,
                reason=reason,
                note=note,
            ),
        )
        event_log.log(
            "access_request", url=url, url_key=url_key, host=host, id=req.id, profile=rt.name
        )
        metrics.record_access_request(host)
        return JSONResponse({"id": req.id, "status": req.status})

    async def review_page(_request: Request) -> FileResponse:
        # Inert HTML shell (no secrets); the data + actions below require the PIN.
        return FileResponse(Path(__file__).parent / "review.html", media_type="text/html")

    async def review_requests(request: Request) -> JSONResponse:
        guard = _require_pin(request)
        if guard is not None:
            return guard
        # One parent reviews every teen: aggregate across profiles, labelling each request
        # with the teen it belongs to so approvals can be routed back to the right whitelist.
        pending: list[dict[str, Any]] = []
        recent: list[dict[str, Any]] = []
        for runtime in profile_runtimes.values():
            snapshot = runtime.request_store.current()
            pending.extend({**asdict(r), "profile": runtime.name} for r in snapshot.pending())
            recent.extend({**asdict(r), "profile": runtime.name} for r in snapshot.recent_decided())
        pending.sort(key=lambda r: r["created_ts"])
        recent.sort(key=lambda r: r.get("decided_ts") or "", reverse=True)
        return JSONResponse({"pending": pending, "recent": recent[:50]})

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

        # Find which teen owns this request (ids are globally unique uuid4); the decision and its
        # side effects apply only to that teen's stores, never another's.
        owner: _ProfileRuntime | None = None
        existing = None
        for runtime in profile_runtimes.values():
            match = runtime.request_store.current().by_id(request_id)
            if match is not None:
                owner = runtime
                existing = match
                break
        if owner is None or existing is None:
            return JSONResponse({"error": "request not found"}, status_code=404)

        # The whitelist entry is the parent's choice; default to the request's RAW url (an exact
        # match) — never the url_key, which collapses YouTube videos to a non-matching topic.
        entry = str(body.get("whitelist_entry", "")).strip()
        if decision == "approve" and not entry:
            entry = existing.url
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
                    owner.request_store.decide,
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
                await loop.run_in_executor(None, owner.whitelist.add, entry)
                await loop.run_in_executor(None, owner.cache.clear)  # an allow invalidates a block
            except OSError:
                return JSONResponse({"error": "whitelist write failed"}, status_code=500)
            event_log.log("access_request_approved", id=request_id, entry=entry, profile=owner.name)
            metrics.record_access_decision("approve")
        else:
            event_log.log("access_request_rejected", id=request_id, profile=owner.name)
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
