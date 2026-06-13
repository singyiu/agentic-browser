"""Routes: POST /dwell, GET /time/state, GET/POST /time-request.

Also exports the shared helpers ``_clamp_request_minutes``, ``_usage_to_json``,
and ``_block_reason`` which are imported by ``review.py`` for the parent-side
time-request decision and usage-summary endpoints.
"""

from __future__ import annotations

import asyncio
import functools
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..normalize import extract_host
from ..time_policy import TimePolicy
from ..time_policy import resolve as resolve_time_policy
from ..whitelist import canonicalize_url
from .deps import GuardianDeps, make_auth_helpers

if TYPE_CHECKING:
    from ..runtime import ProfileRuntime

# ---------------------------------------------------------------------------
# Module-level constants (exact copies from service.py to keep zero-delta).
# ---------------------------------------------------------------------------

_MAX_DWELL_MS: int = 6 * 60 * 60 * 1000
_MAX_TIME_REASON: int = 500
_MAX_REQUEST_MINUTES: int = 1440


# ---------------------------------------------------------------------------
# Pure shared helpers — also used by routes/review.py.
# ---------------------------------------------------------------------------


def _clamp_request_minutes(value: object) -> int | None:
    """A teen's requested-minutes ask: int in [1, 1440], else None (let the parent decide)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    n = int(value)
    return n if 1 <= n <= _MAX_REQUEST_MINUTES else None


def _block_reason(usage: object) -> str:
    """A short machine hint for why the current host is blocked (or "" when it is not)."""
    u = usage
    if not u.blocked:  # type: ignore[attr-defined]
        return ""
    site = u.site  # type: ignore[attr-defined]
    if site is not None and site.excluded:
        return "site_limit" if site.blocked else ""
    if u.bedtime_active:  # type: ignore[attr-defined]
        return "bedtime"
    if u.blocked_general:  # type: ignore[attr-defined]
        return "time_limit"
    if site is not None and site.blocked:
        return "site_limit"
    return ""


def _usage_to_json(usage: object) -> dict[str, Any]:
    """Serialize a :class:`time_ledger.Usage` to the time-state response envelope."""
    u = usage
    site = u.site  # type: ignore[attr-defined]
    return {
        "general": {
            "used_ms": u.general_used_ms,  # type: ignore[attr-defined]
            "limit_ms": u.general_limit_ms,  # type: ignore[attr-defined]
            "remaining_ms": u.general_remaining_ms,  # type: ignore[attr-defined]
            "blocked": u.blocked_general,  # type: ignore[attr-defined]
        },
        "bedtime": {"active": u.bedtime_active},  # type: ignore[attr-defined]
        "site": (
            None
            if site is None
            else {
                "host": site.host,
                "excluded": site.excluded,
                "used_ms": site.used_ms,
                "limit_ms": site.limit_ms,
                "remaining_ms": site.remaining_ms,
                "blocked": site.blocked,
            }
        ),
        "blocked": u.blocked,  # type: ignore[attr-defined]
        "reason": _block_reason(u),
    }


# ---------------------------------------------------------------------------
# Route builder
# ---------------------------------------------------------------------------


def build_routes(deps: GuardianDeps) -> list[Route]:
    """Return [/dwell, /time/state, /time-request] routes bound to *deps*."""
    resolve_runtime, _ = make_auth_helpers(deps)

    def _resolve_time_policy(rt: ProfileRuntime) -> TimePolicy:
        """Effective policy for a teen: its own, with the Global profile layered under it."""
        return resolve_time_policy(
            rt.time_policy.current(), deps.pm.global_runtime().time_policy.current()
        )

    def _time_state(rt: ProfileRuntime, url: str | None, now: datetime) -> dict[str, Any]:
        host = extract_host(url) if url else None
        usage = deps.time_ledger.usage(rt.name, _resolve_time_policy(rt), host, now)
        return _usage_to_json(usage)

    async def dwell(request: Request) -> JSONResponse:
        rt = resolve_runtime(request)
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
            or dwell_ms > _MAX_DWELL_MS
        ):
            return JSONResponse(
                {"error": "url_key and dwell_ms in [0, 6h] required"}, status_code=422
            )
        host = extract_host(url_key)
        now = datetime.now(UTC)
        # Account before logging: the first touch of the day seeds from the log as it stands,
        # so counting this not-yet-logged event separately avoids a double count.
        deps.time_ledger.add_dwell(rt.name, host, int(dwell_ms), now)
        deps.metrics.record_dwell(host, rt.name, float(dwell_ms) / 1000.0)
        deps.event_log.log(
            "dwell", url_key=url_key, host=host, dwell_ms=int(dwell_ms), profile=rt.name
        )
        # Return the current time-state so the extension's heartbeat can enforce immediately.
        return JSONResponse({"ok": True, **_time_state(rt, url_key, now)})

    async def time_state_endpoint(request: Request) -> JSONResponse:
        # Teen-facing: the extension reads its remaining credits + whether to block (token-authed).
        rt = resolve_runtime(request)
        if rt is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        url = request.query_params.get("url", "").strip()
        return JSONResponse(_time_state(rt, url or None, datetime.now(UTC)))

    async def time_request_endpoint(request: Request) -> JSONResponse:
        # Teen-facing: ask a parent for more time (token-authed). Granting is PIN-gated below.
        rt = resolve_runtime(request)
        if rt is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        loop = asyncio.get_running_loop()

        if request.method == "GET":
            target = request.query_params.get("target_host", "").strip() or None
            match = next(
                (
                    r
                    for r in reversed(rt.time_request_store.current().requests)
                    if r.target_host == target
                ),
                None,
            )
            if match is None:
                return JSONResponse({"status": "none"})
            return JSONResponse(
                {
                    "status": match.status,
                    "id": match.id,
                    "granted_minutes": match.granted_minutes,
                    "decision_note": match.decision_note,
                    "target_host": match.target_host,
                }
            )

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        reason = str(body.get("reason", "")).strip()
        note = str(body.get("note", "")).strip()
        if len(reason) > _MAX_TIME_REASON or len(note) > _MAX_TIME_REASON:
            return JSONResponse(
                {"error": f"reason and note must be at most {_MAX_TIME_REASON} chars"},
                status_code=422,
            )
        target_raw = str(body.get("target_host", "")).strip()
        target = canonicalize_url(target_raw).split("/", 1)[0] if target_raw else None
        minutes = _clamp_request_minutes(body.get("requested_minutes"))
        req = await loop.run_in_executor(
            None,
            functools.partial(
                rt.time_request_store.add_request,
                target_host=target,
                requested_minutes=minutes,
                reason=reason,
                note=note,
            ),
        )
        deps.event_log.log(
            "time_request",
            id=req.id,
            profile=rt.name,
            target_host=target,
            requested_minutes=minutes,
        )
        return JSONResponse({"id": req.id, "status": req.status})

    return [
        Route("/dwell", dwell, methods=["POST"]),
        Route("/time/state", time_state_endpoint, methods=["GET"]),
        Route("/time-request", time_request_endpoint, methods=["GET", "POST"]),
    ]
