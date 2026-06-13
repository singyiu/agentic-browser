"""Routes: GET /prize-points, POST /prize-points/redeem, /review/prize-points/*.

Covers teen-facing balance/redeem and parent-facing grant/events endpoints.
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
from ..prize_points import (
    POINTS_PER_MINUTE,
    REDEEM_PACKAGES_MIN,
    cost_for_minutes,
    redeemed_minutes_today,
)
from ..service import (
    _MAX_PRIZE_POINTS,
    PRIZE_EVENTS,
    _clamp_prize_points,
    _parse_activity_limit,
)
from ..time_policy import resolve as resolve_time_policy
from .deps import GuardianDeps, make_auth_helpers
from .time import _usage_to_json

if TYPE_CHECKING:
    from ..runtime import ProfileRuntime


def build_routes(deps: GuardianDeps) -> list[Route]:
    """Return /prize-points and /review/prize-points/* routes bound to *deps*."""
    resolve_runtime, _require_pin = make_auth_helpers(deps)
    event_log = deps.event_log
    time_ledger = deps.time_ledger
    config = deps.config
    metrics = deps.metrics

    def _resolve_time_policy(rt: ProfileRuntime) -> Any:
        return resolve_time_policy(
            rt.time_policy.current(), deps.pm.global_runtime().time_policy.current()
        )

    def _time_state(rt: ProfileRuntime, url: str | None, now: datetime) -> dict[str, Any]:
        host = extract_host(url) if url else None
        usage = time_ledger.usage(rt.name, _resolve_time_policy(rt), host, now)
        return _usage_to_json(usage)

    def _prize_state(rt: ProfileRuntime, now: datetime) -> dict[str, Any]:
        """A teen's prize balance + which redemption packages they can afford right now."""
        balance = rt.prize_point_store.balance()
        start, end = time_ledger.day_bounds_utc(now)
        redeemed = redeemed_minutes_today(event_log, rt.name, start=start, end=end)
        remaining_cap = max(0, config.prize_daily_bonus_cap_min - redeemed)
        return {
            "balance": balance,
            "points_per_minute": POINTS_PER_MINUTE,
            "daily_cap_min": config.prize_daily_bonus_cap_min,
            "remaining_daily_bonus_min": remaining_cap,
            "packages": [
                {
                    "minutes": minutes,
                    "cost": cost_for_minutes(minutes),
                    "affordable": (
                        balance >= cost_for_minutes(minutes) and minutes <= remaining_cap
                    ),
                }
                for minutes in REDEEM_PACKAGES_MIN
            ],
        }

    async def prize_points_state(request: Request) -> JSONResponse:
        # Teen-facing (token): current balance + affordable packages for the popup/block page.
        rt = resolve_runtime(request)
        if rt is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return JSONResponse(_prize_state(rt, datetime.now(UTC)))

    async def prize_points_redeem(request: Request) -> JSONResponse:
        # Teen-facing (token): spend points for bonus minutes — self-serve, no parent PIN.
        rt = resolve_runtime(request)
        if rt is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        minutes = body.get("minutes")
        if (
            isinstance(minutes, bool)
            or not isinstance(minutes, int)
            or minutes not in REDEEM_PACKAGES_MIN
        ):
            return JSONResponse(
                {"error": f"minutes must be one of {list(REDEEM_PACKAGES_MIN)}"},
                status_code=422,
            )
        cost = cost_for_minutes(minutes)
        now = datetime.now(UTC)
        # Enforce the per-day bonus cap (independent of parent grants) before spending.
        start, end = time_ledger.day_bounds_utc(now)
        redeemed = redeemed_minutes_today(event_log, rt.name, start=start, end=end)
        remaining_cap = max(0, config.prize_daily_bonus_cap_min - redeemed)
        if minutes > remaining_cap:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "daily_cap_reached",
                    "balance": rt.prize_point_store.balance(),
                    "remaining_daily_bonus_min": remaining_cap,
                },
                status_code=409,
            )
        # Atomic spend: deduct only if the balance covers it (no double-click race).
        new_balance = rt.prize_point_store.try_spend(cost)
        if new_balance is None:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "insufficient_points",
                    "balance": rt.prize_point_store.balance(),
                },
                status_code=409,
            )
        # Grant the bonus time via the same seam a parent approval uses, then record it.
        time_ledger.add_grant(rt.name, minutes, now)
        event_log.log(
            "prize_points_redeemed",
            profile=rt.name,
            delta=-cost,
            points=cost,
            minutes_granted=minutes,
            balance_after=new_balance,
        )
        metrics.record_prize_redeem(rt.name, cost, new_balance)
        url = request.query_params.get("url", "").strip() or None
        return JSONResponse(
            {
                "ok": True,
                "granted_minutes": minutes,
                "balance": new_balance,
                **_time_state(rt, url, now),
            }
        )

    async def review_prize_points(request: Request) -> JSONResponse:
        # Parent-facing (PIN): each teen's balance + the redemption policy (Prize point page).
        guard = _require_pin(request)
        if guard is not None:
            return guard
        balances = [
            {"profile": rt.name, "balance": rt.prize_point_store.balance()}
            for rt in deps.pm.snapshot().values()
        ]
        return JSONResponse(
            {
                "points_per_minute": POINTS_PER_MINUTE,
                "daily_cap_min": config.prize_daily_bonus_cap_min,
                "packages": [
                    {"minutes": minutes, "cost": cost_for_minutes(minutes)}
                    for minutes in REDEEM_PACKAGES_MIN
                ],
                "balances": balances,
            }
        )

    async def review_prize_grant(request: Request) -> JSONResponse:
        # Parent-facing (PIN): award (or correct) a teen's prize points.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        profile = str(body.get("profile", "")).strip()
        points = _clamp_prize_points(body.get("points"))
        if points is None:
            return JSONResponse(
                {"error": f"points must be a non-zero integer within +-{_MAX_PRIZE_POINTS}"},
                status_code=422,
            )
        owner = deps.pm.snapshot().get(profile)
        if owner is None:
            return JSONResponse({"error": "unknown profile"}, status_code=404)
        raw_reason = body.get("reason")
        reason = raw_reason.strip()[:200] if isinstance(raw_reason, str) else ""
        balance = owner.prize_point_store.add(points)
        event_log.log(
            "prize_points_earned",
            profile=owner.name,
            delta=points,
            reason=reason,
            balance_after=balance,
        )
        metrics.record_prize_grant(owner.name, points, balance)
        return JSONResponse({"ok": True, "profile": owner.name, "balance": balance})

    async def review_prize_events(request: Request) -> JSONResponse:
        # Parent-facing (PIN): the prize-point event feed for the Activity "Prize points" tab.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        profile = request.query_params.get("profile", "").strip() or None
        limit = _parse_activity_limit(request.query_params.get("limit"))
        loop = asyncio.get_running_loop()
        events = await loop.run_in_executor(
            None,
            functools.partial(event_log.recent, limit, profile=profile, events=PRIZE_EVENTS),
        )
        return JSONResponse({"events": events})

    return [
        Route("/prize-points", prize_points_state, methods=["GET"]),
        Route("/prize-points/redeem", prize_points_redeem, methods=["POST"]),
        Route("/review/prize-points", review_prize_points, methods=["GET"]),
        Route("/review/prize-points/grant", review_prize_grant, methods=["POST"]),
        Route("/review/prize-points/events", review_prize_events, methods=["GET"]),
    ]
