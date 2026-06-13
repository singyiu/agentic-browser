"""Routes: GET/POST/DELETE /whitelist, GET/POST /access-request, GET/POST /search-request.

All three are token-gated (X-Guardian-Token).  Whitelist mutations additionally
require the parent PIN (X-Guardian-Parent-Pin).
"""
from __future__ import annotations

import asyncio
import functools

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..normalize import extract_host, normalize_url
from ..whitelist import classify_entry
from .deps import GuardianDeps, make_auth_helpers

_MAX_QUERY_CHARS = 500


def build_routes(deps: GuardianDeps) -> list[Route]:
    """Return [/whitelist, /access-request, /search-request] routes bound to *deps*."""
    resolve_runtime, require_pin = make_auth_helpers(deps)

    async def whitelist_endpoint(request: Request) -> JSONResponse:
        rt = resolve_runtime(request)
        if rt is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if request.method == "GET":
            entries = [
                {"value": value, "type": classify_entry(value)}
                for value in rt.whitelist.current().values
            ]
            return JSONResponse({"entries": entries})
        # Mutations are parent-only: the kid whitelist outranks the Global blocklist
        # ("individual wins"), so a kid-held token alone must never add entries.
        guard = require_pin(request)
        if guard is not None:
            return guard
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
        deps.event_log.log(f"whitelist_{request.method.lower()}", entry=entry, profile=rt.name)
        if request.method == "POST":
            return JSONResponse({"value": entry, "type": classify_entry(entry)})
        return JSONResponse({"ok": True})

    async def access_request_endpoint(request: Request) -> JSONResponse:
        # Teen-facing: the extension already holds X-Guardian-Token, so submitting/checking a
        # request is low-privilege. Approving is NOT here — that needs the parent PIN (below).
        rt = resolve_runtime(request)
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
        deps.event_log.log(
            "access_request", url=url, url_key=url_key, host=host, id=req.id, profile=rt.name
        )
        deps.metrics.record_access_request(host)
        return JSONResponse({"id": req.id, "status": req.status})

    async def search_request_endpoint(request: Request) -> JSONResponse:
        # Teen-facing: ask a parent to allow a blocked search keyword (token-authed, low-privilege).
        rt = resolve_runtime(request)
        if rt is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        loop = asyncio.get_running_loop()

        if request.method == "GET":
            query = request.query_params.get("query", "").strip()
            if not query or len(query) > _MAX_QUERY_CHARS:
                return JSONResponse({"error": "query param required"}, status_code=422)
            match = rt.request_store.current().latest_for_keyword(query)
            if match is None:
                return JSONResponse({"status": "none"})
            return JSONResponse(
                {"status": match.status, "id": match.id, "decision_note": match.decision_note}
            )

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        query = str(body.get("query", "")).strip()
        page_url = str(body.get("url", "")).strip()
        note = str(body.get("note", "")).strip()
        if not query or len(query) > _MAX_QUERY_CHARS:
            return JSONResponse(
                {"error": f"query required (1-{_MAX_QUERY_CHARS} chars)"}, status_code=422
            )
        if not page_url or len(page_url) > 2048 or not page_url.startswith(("http://", "https://")):
            return JSONResponse({"error": "url must be an http(s) URL"}, status_code=422)
        if len(note) > 500:
            return JSONResponse({"error": "note must be at most 500 chars"}, status_code=422)
        url_key = normalize_url(page_url)
        req = await loop.run_in_executor(
            None,
            functools.partial(
                rt.request_store.add_request,
                url=page_url,
                url_key=url_key,
                host=extract_host(url_key),
                reason=f"Blocked search: {query[:200]}",
                note=note,
                kind="search",
                keyword=query,
            ),
        )
        # Log only the keyword length; the parent reviews the keyword itself in the stored request.
        deps.event_log.log("search_request", query_len=len(query), id=req.id, profile=rt.name)
        return JSONResponse({"id": req.id, "status": req.status})

    return [
        Route("/whitelist", whitelist_endpoint, methods=["GET", "POST", "DELETE"]),
        Route("/access-request", access_request_endpoint, methods=["GET", "POST"]),
        Route("/search-request", search_request_endpoint, methods=["GET", "POST"]),
    ]
