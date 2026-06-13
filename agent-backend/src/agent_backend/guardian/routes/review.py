"""Routes: /review/* requests, decisions, time policy; /time/policy.

Covers:
  GET  /review/requests
  POST /review/decision
  GET  /review/time-requests
  POST /review/time-decision
  GET  /review/time/usage
  GET  PUT /time/policy
  POST /time/policy/parse
"""
from __future__ import annotations

import asyncio
import functools
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..access_requests import AccessRequest
from ..profiles import DEFAULT_PROFILE_NAME, GLOBAL_PROFILE_NAME
from ..service import (
    _MAX_PROMPT_CHARS,
    _MAX_TIME_TEXT,
    _TIME_POLICY_SYSTEM_PROMPT,
    _valid_prompt_text,
)
from ..time_policy import TimePolicyStore
from ..time_policy import from_stored as time_policy_from_stored
from ..time_policy import parse_policy as parse_time_policy
from ..time_policy import resolve as resolve_time_policy
from ..time_policy import to_json as time_policy_to_json
from .deps import GuardianDeps, make_auth_helpers
from .time import _MAX_REQUEST_MINUTES, _clamp_request_minutes

if TYPE_CHECKING:
    from ..runtime import ProfileRuntime


def build_routes(deps: GuardianDeps) -> list[Route]:
    """Return review request/decision and time-policy routes bound to *deps*."""
    _, _require_pin = make_auth_helpers(deps)
    event_log = deps.event_log
    metrics = deps.metrics
    time_ledger = deps.time_ledger
    classifier = deps.classifier
    config = deps.config

    def _time_policy_store_for(name: str) -> TimePolicyStore | None:
        if name.lower() == GLOBAL_PROFILE_NAME:
            return deps.pm.global_runtime().time_policy
        rt = deps.pm.snapshot().get(name)
        return rt.time_policy if rt is not None else None

    async def _clear_caches_after_list_change(rt: ProfileRuntime) -> None:
        # A Global edit affects every teen, so clear all teen caches; a per-teen edit clears
        # just that teen's.
        loop = asyncio.get_running_loop()
        targets = (
            list(deps.pm.snapshot().values()) if rt.name == GLOBAL_PROFILE_NAME else [rt]
        )
        for target in targets:
            await loop.run_in_executor(None, target.cache.clear)

    async def _apply_block_rule(
        owner: ProfileRuntime,
        *,
        scope: str,
        rule: str | None,
        hard: bool,
        kind: str,
        existing: AccessRequest,
    ) -> tuple[bool, bool]:
        """On reject, optionally add a generalized block for *similar* content.

        Two complementary effects, both optional and scoped to this teen (``"profile"``) or the
        shared Global profile (``"global"``):
          - ``rule``: a natural-language line appended to the classifier prompt (semantic match).
          - ``hard``: the exact host (URL) or keyword (search) added to the hard list (AI-free).

        Strictly best-effort: the reject is already persisted, so a rejected/oversized/failed
        write is logged and reported as not-applied, never raised. Returns
        ``(rule_applied, hard_block_applied)``.
        """
        if not rule and not hard:
            return False, False
        target = deps.pm.global_runtime() if scope == "global" else owner
        loop = asyncio.get_running_loop()
        applied_rule = False
        applied_hard = False
        if rule:
            if not _valid_prompt_text(rule):
                event_log.log("block_rule_skipped", reason="invalid", profile=target.name)
            else:
                applied_rule = await loop.run_in_executor(
                    None,
                    functools.partial(
                        target.prompt_store.append,
                        rule,
                        separator="\n\n",
                        max_chars=_MAX_PROMPT_CHARS,
                    ),
                )
                event_log.log(
                    "block_rule_added" if applied_rule else "block_rule_skipped",
                    reason=None if applied_rule else "prompt_full",
                    profile=target.name,
                    scope=scope,
                    kind=kind,
                )
        if hard:
            entry = (existing.keyword or "") if kind == "search" else (existing.host or "")
            entry = entry.strip()
            store = target.search_block if kind == "search" else target.blocklist
            if entry and len(entry) <= 512 and entry.isprintable():
                try:
                    await loop.run_in_executor(None, store.add, entry)
                    applied_hard = True
                    event_log.log(
                        "hard_block_added",
                        profile=target.name,
                        scope=scope,
                        kind=kind,
                        entry=entry,
                    )
                except OSError:
                    event_log.log(
                        "hard_block_skipped", reason="write_failed", profile=target.name
                    )
            else:
                event_log.log("hard_block_skipped", reason="no_target", profile=target.name)
        if applied_rule or applied_hard:
            await _clear_caches_after_list_change(target)
        return applied_rule, applied_hard

    async def review_requests(request: Request) -> JSONResponse:
        guard = _require_pin(request)
        if guard is not None:
            return guard
        # One parent reviews every teen: aggregate across profiles, labelling each request
        # with the teen it belongs to so approvals can be routed back to the right whitelist.
        pending: list[dict[str, Any]] = []
        recent: list[dict[str, Any]] = []
        for runtime in deps.pm.snapshot().values():
            snapshot = runtime.request_store.current()
            pending.extend({**asdict(r), "profile": runtime.name} for r in snapshot.pending())
            recent.extend(
                {**asdict(r), "profile": runtime.name} for r in snapshot.recent_decided()
            )
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
        # Optional "block similar content" rule applied only on reject (see _apply_block_rule):
        # a free-text classifier rule and/or a hard list entry, scoped to this teen or Global.
        block_rule = str(body.get("block_rule", "")).strip() or None
        block_hard = bool(body.get("block_hard", False))
        block_scope = (
            "global" if str(body.get("block_scope", "")).strip() == "global" else "profile"
        )
        loop = asyncio.get_running_loop()

        # Find which teen owns this request (ids are globally unique uuid4); the decision and its
        # side effects apply only to that teen's stores, never another's.
        owner: ProfileRuntime | None = None
        existing = None
        for runtime in deps.pm.snapshot().values():
            match = runtime.request_store.current().by_id(request_id)
            if match is not None:
                owner = runtime
                existing = match
                break
        if owner is None or existing is None:
            return JSONResponse({"error": "request not found"}, status_code=404)

        # A search-keyword request approves the keyword onto the teen's search ALLOW list (not the
        # URL whitelist); the keyword is fixed at request time, so no parent-supplied entry.
        if existing.kind == "search":
            try:
                decided = await loop.run_in_executor(
                    None,
                    functools.partial(
                        owner.request_store.decide,
                        request_id,
                        decision=decision,
                        whitelist_entry=existing.keyword if decision == "approve" else None,
                        decision_note=note,
                    ),
                )
            except KeyError:
                return JSONResponse({"error": "request not found"}, status_code=404)
            except ValueError:
                return JSONResponse({"error": "request already decided"}, status_code=422)
            if decision == "approve" and existing.keyword:
                try:
                    await loop.run_in_executor(None, owner.search_allow.add, existing.keyword)
                    await loop.run_in_executor(None, owner.cache.clear)
                except OSError:
                    return JSONResponse({"error": "keyword write failed"}, status_code=500)
                event_log.log("search_request_approved", id=request_id, profile=owner.name)
                metrics.record_access_decision("approve")
                return JSONResponse({"id": decided.id, "status": decided.status})
            event_log.log("search_request_rejected", id=request_id, profile=owner.name)
            metrics.record_access_decision("reject")
            applied_rule, applied_hard = await _apply_block_rule(
                owner,
                scope=block_scope,
                rule=block_rule,
                hard=block_hard,
                kind="search",
                existing=existing,
            )
            return JSONResponse(
                {
                    "id": decided.id,
                    "status": decided.status,
                    "rule_applied": applied_rule,
                    "hard_block_applied": applied_hard,
                }
            )

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
            event_log.log(
                "access_request_approved", id=request_id, entry=entry, profile=owner.name
            )
            metrics.record_access_decision("approve")
            return JSONResponse({"id": decided.id, "status": decided.status})
        event_log.log("access_request_rejected", id=request_id, profile=owner.name)
        metrics.record_access_decision("reject")
        applied_rule, applied_hard = await _apply_block_rule(
            owner,
            scope=block_scope,
            rule=block_rule,
            hard=block_hard,
            kind=existing.kind,
            existing=existing,
        )
        return JSONResponse(
            {
                "id": decided.id,
                "status": decided.status,
                "rule_applied": applied_rule,
                "hard_block_applied": applied_hard,
            }
        )

    async def review_time_requests(request: Request) -> JSONResponse:
        # Parent-facing (PIN): pending + recently decided "more time" asks across all teens.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        pending: list[dict[str, Any]] = []
        recent: list[dict[str, Any]] = []
        for rt in deps.pm.snapshot().values():
            snap = rt.time_request_store.current()
            for r in snap.pending():
                pending.append({**asdict(r), "profile": rt.name, "age": rt.age})
            for r in snap.recent_decided():
                recent.append({**asdict(r), "profile": rt.name})
        pending.sort(key=lambda r: r["created_ts"])
        recent.sort(key=lambda r: r.get("decided_ts") or "", reverse=True)
        return JSONResponse({"pending": pending, "recent": recent[:50]})

    async def review_time_decision(request: Request) -> JSONResponse:
        # Parent-facing (PIN): grant bonus minutes (approve) or deny a time request.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        request_id = str(body.get("id", "")).strip()
        profile = str(body.get("profile", "")).strip()
        decision = str(body.get("decision", "")).strip()
        if decision not in ("approve", "reject"):
            return JSONResponse({"error": "decision must be approve|reject"}, status_code=422)
        owner = deps.pm.snapshot().get(profile)
        if owner is None:
            return JSONResponse({"error": "unknown profile"}, status_code=404)
        granted = _clamp_request_minutes(body.get("granted_minutes"))
        if decision == "approve" and granted is None:
            return JSONResponse(
                {"error": f"granted_minutes must be an integer 1..{_MAX_REQUEST_MINUTES}"},
                status_code=422,
            )
        raw_note = body.get("note")
        note = raw_note.strip() if isinstance(raw_note, str) else None
        loop = asyncio.get_running_loop()
        try:
            decided = await loop.run_in_executor(
                None,
                functools.partial(
                    owner.time_request_store.decide,
                    request_id,
                    decision=decision,
                    granted_minutes=granted,
                    decision_note=note,
                ),
            )
        except KeyError:
            return JSONResponse({"error": "request not found"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        if decision == "approve" and granted:
            now = datetime.now(UTC)
            # A grant extends today's general pool (event-sourced; expires at day rollover).
            time_ledger.add_grant(owner.name, granted, now)
            event_log.log(
                "time_grant",
                profile=owner.name,
                minutes=granted,
                target_host=decided.target_host,
                request_id=decided.id,
            )
        return JSONResponse(
            {
                "id": decided.id,
                "status": decided.status,
                "granted_minutes": decided.granted_minutes,
            }
        )

    async def time_policy_endpoint(request: Request) -> JSONResponse:
        # Parent-facing (PIN): read / replace a profile's (or Global's) structured time policy.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        name = request.query_params.get("profile", "").strip()
        store = _time_policy_store_for(name)
        if store is None:
            return JSONResponse({"error": "unknown profile"}, status_code=404)
        is_global = name.lower() == GLOBAL_PROFILE_NAME
        if request.method == "GET":
            own = store.current()
            glob = deps.pm.global_runtime().time_policy.current()
            effective = own if is_global else resolve_time_policy(own, glob)
            return JSONResponse(
                {
                    "profile": name,
                    "is_global": is_global,
                    "policy": time_policy_to_json(own),
                    "effective": time_policy_to_json(effective),
                }
            )
        # PUT: replace with the structured policy (validated + clamped), stamped server-side.
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        if not isinstance(body, dict):
            return JSONResponse({"error": "policy object required"}, status_code=422)
        source = body.get("source_text")
        if isinstance(source, str) and len(source) > _MAX_TIME_TEXT:
            return JSONResponse({"error": "source_text too long"}, status_code=422)
        body["updated_ts"] = datetime.now(UTC).isoformat()
        policy = time_policy_from_stored(body)
        loop = asyncio.get_running_loop()
        try:
            saved = await loop.run_in_executor(None, store.set, policy)
        except OSError:
            return JSONResponse({"error": "policy write failed"}, status_code=500)
        event_log.log("time_policy_set", profile=name or DEFAULT_PROFILE_NAME)
        return JSONResponse({"ok": True, "policy": time_policy_to_json(saved)})

    async def time_policy_parse(request: Request) -> JSONResponse:
        # Parent-facing (PIN): turn natural-language limits into a structured policy (preview only).
        guard = _require_pin(request)
        if guard is not None:
            return guard
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        text = str(body.get("text", "")).strip()
        if not text or len(text) > _MAX_TIME_TEXT:
            return JSONResponse(
                {"error": f"text required (max {_MAX_TIME_TEXT} chars)"}, status_code=422
            )
        try:
            raw = await asyncio.wait_for(
                classifier.generate(
                    system_prompt=_TIME_POLICY_SYSTEM_PROMPT, user_prompt=text
                ),
                timeout=config.classify_timeout_s,
            )
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "time policy parse failed"}, status_code=502)
        policy = parse_time_policy(raw, source_text=text)
        return JSONResponse({"policy": time_policy_to_json(policy)})

    async def review_time_usage(request: Request) -> JSONResponse:
        # Parent-facing (PIN): each teen's today used/remaining for the dashboard live view.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        now = datetime.now(UTC)
        glob = deps.pm.global_runtime().time_policy.current()
        out: list[dict[str, Any]] = []
        for rt in deps.pm.snapshot().values():
            policy = resolve_time_policy(rt.time_policy.current(), glob)
            usage = time_ledger.usage(rt.name, policy, None, now)
            out.append(
                {
                    "profile": rt.name,
                    "age": rt.age,
                    "has_policy": policy.is_set(),
                    "general": {
                        "used_ms": usage.general_used_ms,
                        "limit_ms": usage.general_limit_ms,
                        "remaining_ms": usage.general_remaining_ms,
                        "blocked": usage.blocked_general,
                    },
                    "bedtime_active": usage.bedtime_active,
                }
            )
        return JSONResponse({"profiles": out})

    return [
        Route("/review/requests", review_requests, methods=["GET"]),
        Route("/review/decision", review_decision, methods=["POST"]),
        Route("/review/time-requests", review_time_requests, methods=["GET"]),
        Route("/review/time-decision", review_time_decision, methods=["POST"]),
        Route("/review/time/usage", review_time_usage, methods=["GET"]),
        Route("/time/policy", time_policy_endpoint, methods=["GET", "PUT"]),
        Route("/time/policy/parse", time_policy_parse, methods=["POST"]),
    ]
