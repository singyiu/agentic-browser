"""Routes: /review/suggest-block-rule, /review/whitelist, /review/blocklist,
/review/search-keywords/*, /review/activity/*, and /review/prompt.

All endpoints are PIN-gated parent-only views and writes over per-profile stores.
"""
from __future__ import annotations

import asyncio
import functools
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..config import DEFAULT_AGE, MAX_AGE, MIN_AGE
from ..normalize import extract_host
from ..profiles import GLOBAL_PROFILE_NAME
from ..prompt import default_profile_prompt
from ..service import (
    _MAX_PROMPT_CHARS,
    _MAX_RULE_SUGGESTIONS,
    ACTIVITY_EVENTS,
    SUMMARY_LIMIT_DEFAULT,
    SUMMARY_STALE_AFTER_S,
    _activity_digest,
    _parse_activity_limit,
    _parse_activity_summary,
    _parse_rule_suggestions,
    _summarize_activity,
    _summary_is_stale,
    _valid_prompt_text,
)
from ..whitelist import classify_entry
from .deps import GuardianDeps, make_auth_helpers

if TYPE_CHECKING:
    from ..runtime import ProfileRuntime


def build_routes(deps: GuardianDeps) -> list[Route]:
    """Return review-activity, list-management, and prompt routes bound to *deps*."""
    _, _require_pin = make_auth_helpers(deps)
    event_log = deps.event_log
    summary_log = deps.summary_log
    classifier = deps.classifier
    config = deps.config

    def _resolve_parent_profile(name: str) -> ProfileRuntime | None:
        """Pick the profile a parent list write targets.

        Parents authenticate with the PIN (not a teen token), so the request carries no
        profile. ``"global"`` targets the shared Global profile; an explicit teen name targets
        that teen; an empty name auto-resolves to the sole teen (the common case), else an
        ambiguous multi-teen write must name one.
        """
        if name == GLOBAL_PROFILE_NAME:
            return deps.pm.global_runtime()
        current = deps.pm.snapshot()
        if name:
            return current.get(name)
        if len(current) == 1:
            return next(iter(current.values()))
        return None

    async def _clear_caches_after_list_change(rt: ProfileRuntime) -> None:
        # A Global edit affects every teen, so clear all teen caches; a per-teen edit clears
        # just that teen's. (Hard URL rules are checked before the cache, so this matters
        # mainly for natural-language topic edits, which only the AI path consults.)
        loop = asyncio.get_running_loop()
        targets = (
            list(deps.pm.snapshot().values()) if rt.name == GLOBAL_PROFILE_NAME else [rt]
        )
        for target in targets:
            await loop.run_in_executor(None, target.cache.clear)

    def _summarize_existing_rules(profile: str | None, *, max_per: int = 40) -> str:
        # Compact digest of the block rules already in force (per-teen + Global), so the model
        # avoids re-suggesting what's covered. Scoped to one teen when a profile filter is set.
        snap = deps.pm.snapshot()
        teens = [snap[profile]] if profile and profile in snap else list(snap.values())
        lines: list[str] = []
        for rt in [*teens, deps.pm.global_runtime()]:
            values = list(rt.blocklist.current().values)
            if values:
                lines.append(f"{rt.name} blocklist: " + ", ".join(values[:max_per]))
            guidance = rt.prompt_store.current().strip()
            if guidance:
                lines.append(f"{rt.name} guidance: {guidance[:400]}")
        return "\n".join(lines) if lines else "(no rules yet)"

    async def review_suggest_block_rule(request: Request) -> JSONResponse:
        # Optional aid for the reject flow: draft a natural-language "block similar content"
        # rule from a request's details. PIN-gated and READ-ONLY (no store mutation); strictly
        # best-effort, so any failure here returns an error the UI can shrug off — it never
        # blocks the guardian from rejecting.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        request_id = str(body.get("id", "")).strip()
        if not request_id:
            return JSONResponse({"error": "id required"}, status_code=422)

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

        system_prompt = (
            "You help a guardian write a short content-blocking rule. Reply with ONLY 1-2 plain "
            "sentences naming the CATEGORY of content to block for similar future requests. Do "
            "not name the specific site or URL. Start with 'Block'. Keep it under 200 characters."
        )
        if existing.kind == "search":
            user_prompt = (
                f"A child (age {owner.age}) searched: {existing.keyword!r}\n"
                f"Blocked reason: {existing.reason}\n"
                f"Child note: {existing.note or '(none)'}\n\n"
                "Describe the category of search topics to block going forward."
            )
        else:
            user_prompt = (
                f"Child age: {owner.age}\n"
                f"Site: {existing.host}\n"
                f"Blocked reason: {existing.reason}\n"
                f"Child note: {existing.note or '(none)'}\n\n"
                "Describe the category of websites to block going forward."
            )

        try:
            raw = await asyncio.wait_for(
                classifier.generate(system_prompt=system_prompt, user_prompt=user_prompt),
                timeout=config.classify_timeout_s,
            )
        except Exception:  # noqa: BLE001 - best-effort; the reject path never depends on this
            return JSONResponse({"error": "rule generation failed"}, status_code=502)
        rule = raw.strip()[:300]
        if not rule:
            return JSONResponse({"error": "empty rule"}, status_code=502)
        event_log.log("suggest_block_rule", id=request_id, profile=owner.name, kind=existing.kind)
        return JSONResponse({"rule": rule})

    async def review_activity_suggest_rule(request: Request) -> JSONResponse:
        # Draft a natural-language "block similar content" rule from one Activity item (a browsed
        # URL), feeding the Activity-page rule builder's "AI-suggested" kind. PIN-gated, READ-ONLY,
        # best-effort: mirrors review_suggest_block_rule but is seeded by {url} (an activity row)
        # rather than a stored access-request id, so it needs no request lookup.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        url = str(body.get("url", "")).strip()
        if not url:
            return JSONResponse({"error": "url required"}, status_code=422)

        # Age tunes the prompt; an unresolved/ambiguous profile just falls back to the default.
        rt = _resolve_parent_profile(str(body.get("profile", "")).strip())
        age = rt.age if rt is not None else DEFAULT_AGE
        host = extract_host(url) or url
        event = str(body.get("event", "")).strip()
        outcome = "was blocked" if event in ("block", "blocklist_block") else "was visited"

        system_prompt = (
            "You help a guardian write a short content-blocking rule. Reply with ONLY 1-2 plain "
            "sentences naming the CATEGORY of content to block for similar sites. Do not name the "
            "specific site or URL. Start with 'Block'. Keep it under 200 characters."
        )
        user_prompt = (
            f"Child age: {age}\n"
            f"Site: {host}\n"
            f"This page {outcome}; the guardian wants to block similar content going forward.\n\n"
            "Describe the category of websites to block."
        )
        try:
            raw = await asyncio.wait_for(
                classifier.generate(system_prompt=system_prompt, user_prompt=user_prompt),
                timeout=config.classify_timeout_s,
            )
        except Exception:  # noqa: BLE001 - best-effort; the builder degrades to manual entry
            return JSONResponse({"error": "rule generation failed"}, status_code=502)
        rule = raw.strip()[:300]
        if not rule:
            return JSONResponse({"error": "empty rule"}, status_code=502)
        event_log.log(
            "activity_suggest_rule", host=host, profile=(rt.name if rt is not None else "")
        )
        return JSONResponse({"rule": rule})

    async def review_activity_suggest_rules(request: Request) -> JSONResponse:
        # Bulk "what should I block next?" helper for the Activity page: summarize recent activity
        # plus the rules already in force, then ask the model for NEW block-rule suggestions. PIN-
        # gated and READ-ONLY (returns drafts; the guardian applies them via /review/blocklist).
        # The output is parsed fail-safe, so a confused model yields no suggestions, never a 500.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        profile = str(body.get("profile", "")).strip() or None
        raw_limit = body.get("limit")
        limit = _parse_activity_limit(None if raw_limit is None else str(raw_limit))

        loop = asyncio.get_running_loop()
        events = await loop.run_in_executor(
            None,
            functools.partial(event_log.recent, limit, profile=profile, events=ACTIVITY_EVENTS),
        )
        if not events:
            # No activity to reason about — skip the LLM call entirely.
            return JSONResponse({"suggestions": []})

        system_prompt = (
            "You help a guardian tighten web filtering for their kids. Given recent browsing "
            "activity and the rules already in force, propose UP TO "
            f"{_MAX_RULE_SUGGESTIONS} NEW blocking rules not already covered. Reply with ONLY a "
            'JSON array; each element an object {"kind","value","reason"} where kind is "exact" (a '
            'hostname), "wildcard" (a host/path containing *), or "nl" (a short natural-language '
            "topic). Keep value under 200 chars and reason to one sentence. If nothing new is "
            "worth blocking, reply with []."
        )
        user_prompt = (
            f"RECENT ACTIVITY:\n{_summarize_activity(events)}\n\n"
            f"RULES ALREADY IN FORCE:\n{_summarize_existing_rules(profile)}\n\n"
            "Propose new blocking rules as a JSON array."
        )
        try:
            raw = await asyncio.wait_for(
                classifier.generate(system_prompt=system_prompt, user_prompt=user_prompt),
                timeout=config.classify_timeout_s,
            )
        except Exception:  # noqa: BLE001 - best-effort; the page degrades to manual rule creation
            return JSONResponse({"error": "rule suggestion failed"}, status_code=502)
        suggestions = _parse_rule_suggestions(raw)
        event_log.log("activity_suggest_rules", profile=profile or "", count=len(suggestions))
        return JSONResponse({"suggestions": suggestions})

    async def review_activity_summary(request: Request) -> JSONResponse:
        # Dashboard panel. GET returns the latest saved per-profile summary plus a staleness
        # flag (no LLM); POST reviews recent activity, writes one timestamped run, and returns
        # it. PIN-gated; generation is fail-safe (malformed model output → empty, never 500).
        guard = _require_pin(request)
        if guard is not None:
            return guard
        loop = asyncio.get_running_loop()
        has_activity = bool(
            await loop.run_in_executor(
                None, functools.partial(event_log.recent, 1, events=ACTIVITY_EVENTS)
            )
        )
        if request.method == "GET":
            latest = await loop.run_in_executor(None, functools.partial(summary_log.recent, 1))
            if latest:
                record = latest[0]
                ts = str(record.get("ts") or "")
                profiles = record.get("profiles")
                return JSONResponse(
                    {
                        "generated_at": ts or None,
                        "stale": _summary_is_stale(ts),
                        "has_activity": has_activity,
                        "profiles": profiles if isinstance(profiles, list) else [],
                    }
                )
            # Nothing saved yet → "stale" (worth generating) only if there's activity to review.
            return JSONResponse(
                {
                    "generated_at": None,
                    "stale": has_activity,
                    "has_activity": has_activity,
                    "profiles": [],
                }
            )

        # POST: generate a fresh summary and persist one timestamped run.
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        raw_limit = body.get("limit") if isinstance(body, dict) else None
        limit = _parse_activity_limit(
            str(raw_limit) if raw_limit is not None else str(SUMMARY_LIMIT_DEFAULT)
        )
        events = await loop.run_in_executor(
            None, functools.partial(event_log.recent, limit, events=ACTIVITY_EVENTS)
        )
        if not events:
            # No activity to summarize — skip the LLM and don't record an empty run.
            return JSONResponse(
                {"generated_at": None, "stale": False, "has_activity": False, "profiles": []}
            )
        ages: dict[str, int] = {name: rt.age for name, rt in deps.pm.snapshot().items()}
        system_prompt = (
            "You are reviewing a child's recent web browsing for their parent or guardian. For "
            "EACH profile in the activity below, write a short, factual review. Reply with ONLY a "
            'JSON object {"profiles":[{"profile","summary","trends","attention"}]} where summary '
            "is 1-3 plain sentences and trends/attention are arrays of short phrases. Include a "
            'profile only if it has activity. Under "attention" call out: repeated attempts to '
            "reach blocked sites; risky or age-inappropriate content; new or unusual sites; "
            "browsing at late-night / unusual hours; and anything else a guardian should notice. "
            "Use empty arrays when nothing applies. Be concise and factual; never invent activity "
            "that isn't listed."
        )
        try:
            raw = await asyncio.wait_for(
                classifier.generate(
                    system_prompt=system_prompt, user_prompt=_activity_digest(events, ages)
                ),
                timeout=config.classify_timeout_s,
            )
        except Exception:  # noqa: BLE001 - best-effort; the dashboard keeps the prior summary
            return JSONResponse({"error": "summary generation failed"}, status_code=502)
        # The summary is per real child — never the shared Global profile (it's fed only teen
        # activity, but drop any Global entry the model emits anyway).
        profiles_list = [
            p
            for p in _parse_activity_summary(raw)["profiles"]
            if p["profile"].strip().lower() != GLOBAL_PROFILE_NAME
        ]
        generated_at = datetime.now(UTC).isoformat()
        await loop.run_in_executor(
            None,
            functools.partial(
                summary_log.log,
                "activity_summary",
                event_count=len(events),
                period_hours=SUMMARY_STALE_AFTER_S // 3600,
                profiles=profiles_list,
            ),
        )
        return JSONResponse(
            {
                "generated_at": generated_at,
                "stale": False,
                "has_activity": True,
                "profiles": profiles_list,
            }
        )

    async def review_activity_summaries(request: Request) -> JSONResponse:
        # Activity-page "Summaries" tab: the saved summary runs, newest first. PIN-gated, no LLM.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        limit = _parse_activity_limit(request.query_params.get("limit"))
        loop = asyncio.get_running_loop()
        runs = await loop.run_in_executor(None, functools.partial(summary_log.recent, limit))
        return JSONResponse({"summaries": runs})

    async def _review_list(request: Request, kind: str) -> JSONResponse:
        # Shared parent-facing allow/deny list management (kind = "whitelist" | "blocklist"),
        # gated by the PIN (the teen-token /whitelist is the extension's path). GET aggregates
        # across every teen + Global; a write targets one profile's store.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        runtimes = [*deps.pm.snapshot().values(), deps.pm.global_runtime()]
        if request.method == "GET":
            entries = [
                {"value": value, "type": classify_entry(value), "profile": rt.name}
                for rt in runtimes
                for value in (rt.blocklist if kind == "blocklist" else rt.whitelist)
                .current()
                .values
            ]
            return JSONResponse({"entries": entries})
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        entry = str(body.get("entry", "")).strip()
        # A single bounded line (entries are injected verbatim into the classifier prompt, so
        # reject newlines / control chars).
        if not entry or len(entry) > 512 or not entry.isprintable():
            return JSONResponse(
                {"error": "entry must be a non-empty, single-line string (max 512 chars)"},
                status_code=422,
            )
        rt = _resolve_parent_profile(str(body.get("profile", "")).strip())
        if rt is None:
            return JSONResponse({"error": "profile required"}, status_code=422)
        store = rt.blocklist if kind == "blocklist" else rt.whitelist
        loop = asyncio.get_running_loop()
        try:
            if request.method == "POST":
                await loop.run_in_executor(None, store.add, entry)
            else:  # DELETE
                await loop.run_in_executor(None, store.remove, entry)
            await _clear_caches_after_list_change(rt)
        except OSError:
            return JSONResponse({"error": f"{kind} write failed"}, status_code=500)
        event_log.log(f"parent_{kind}_{request.method.lower()}", entry=entry, profile=rt.name)
        if request.method == "POST":
            return JSONResponse({"value": entry, "type": classify_entry(entry)})
        return JSONResponse({"ok": True})

    async def review_whitelist(request: Request) -> JSONResponse:
        return await _review_list(request, "whitelist")

    async def review_blocklist(request: Request) -> JSONResponse:
        return await _review_list(request, "blocklist")

    async def _review_search_keywords(request: Request, kind: str) -> JSONResponse:
        # Parent-facing search-keyword list management (kind = "allow" | "block"), PIN-gated.
        # GET aggregates across every teen + Global; a write targets one profile's keyword store.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        runtimes = [*deps.pm.snapshot().values(), deps.pm.global_runtime()]
        if request.method == "GET":
            entries = [
                {"value": value, "profile": rt.name}
                for rt in runtimes
                for value in (rt.search_allow if kind == "allow" else rt.search_block)
                .current()
                .values
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
        rt = _resolve_parent_profile(str(body.get("profile", "")).strip())
        if rt is None:
            return JSONResponse({"error": "profile required"}, status_code=422)
        store = rt.search_allow if kind == "allow" else rt.search_block
        loop = asyncio.get_running_loop()
        try:
            if request.method == "POST":
                await loop.run_in_executor(None, store.add, entry)
            else:  # DELETE
                await loop.run_in_executor(None, store.remove, entry)
            await _clear_caches_after_list_change(rt)
        except OSError:
            return JSONResponse({"error": "search-keyword write failed"}, status_code=500)
        event_log.log(
            f"parent_search_{kind}_{request.method.lower()}", entry=entry, profile=rt.name
        )
        if request.method == "POST":
            return JSONResponse({"value": entry})
        return JSONResponse({"ok": True})

    async def review_search_allow(request: Request) -> JSONResponse:
        return await _review_search_keywords(request, "allow")

    async def review_search_block(request: Request) -> JSONResponse:
        return await _review_search_keywords(request, "block")

    async def review_activity(request: Request) -> JSONResponse:
        # Read-only parent view of recent per-URL verdicts (PIN-gated). No mutation; admin/dwell
        # events are filtered out so the timeline shows only what each kid saw and how it ended.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        profile = request.query_params.get("profile", "").strip() or None
        limit = _parse_activity_limit(request.query_params.get("limit"))
        loop = asyncio.get_running_loop()
        events = await loop.run_in_executor(
            None,
            functools.partial(event_log.recent, limit, profile=profile, events=ACTIVITY_EVENTS),
        )
        return JSONResponse({"events": events})

    async def review_prompt(request: Request) -> JSONResponse:
        # Parent-facing per-profile (or Global) classification-prompt view/edit, PIN-gated. GET
        # returns the stored prompt, the age-band default, the effective merged guidance, and (for
        # a teen) the age. POST saves the prompt + optional age, then clears the verdict cache(s)
        # so the change applies on the next classification.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        if request.method == "GET":
            rt = _resolve_parent_profile(request.query_params.get("profile", "").strip())
            if rt is None:
                return JSONResponse({"error": "profile required"}, status_code=422)
            is_global = rt.name == GLOBAL_PROFILE_NAME
            return JSONResponse(
                {
                    "profile": rt.name,
                    "is_global": is_global,
                    "age": None if is_global else rt.age,
                    "prompt": rt.prompt_store.current(),
                    "default": "" if is_global else default_profile_prompt(rt.age),
                    "merged": "" if is_global else deps.pm.merged_policy(rt),
                }
            )
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        rt = _resolve_parent_profile(str(body.get("profile", "")).strip())
        if rt is None:
            return JSONResponse({"error": "profile required"}, status_code=422)
        prompt = str(body.get("prompt", ""))
        if not _valid_prompt_text(prompt):
            return JSONResponse(
                {"error": f"prompt must be printable text up to {_MAX_PROMPT_CHARS} chars"},
                status_code=422,
            )
        # Validate the optional age fully before any write, so a bad age never leaves a
        # half-applied edit (prompt saved but age rejected).
        is_global = rt.name == GLOBAL_PROFILE_NAME
        new_age: int | None = None
        age_raw = body.get("age")
        if age_raw is not None:
            if is_global:
                return JSONResponse({"error": "the Global profile has no age"}, status_code=422)
            try:
                new_age = int(age_raw)
            except (TypeError, ValueError):
                return JSONResponse({"error": "age must be an integer"}, status_code=422)
            if not MIN_AGE <= new_age <= MAX_AGE:
                return JSONResponse(
                    {"error": f"age must be between {MIN_AGE} and {MAX_AGE}"}, status_code=422
                )
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, rt.prompt_store.set, prompt)
            if new_age is not None:
                rt = deps.pm.set_age(rt.name, new_age)
            await _clear_caches_after_list_change(rt)
        except OSError:
            return JSONResponse({"error": "prompt write failed"}, status_code=500)
        event_log.log(
            "parent_prompt_post",
            profile=rt.name,
            age=None if is_global else rt.age,
            length=len(prompt),
        )
        return JSONResponse({"ok": True, "age": None if is_global else rt.age})

    return [
        Route(
            "/review/suggest-block-rule",
            review_suggest_block_rule,
            methods=["POST"],
        ),
        Route("/review/whitelist", review_whitelist, methods=["GET", "POST", "DELETE"]),
        Route("/review/blocklist", review_blocklist, methods=["GET", "POST", "DELETE"]),
        Route(
            "/review/search-keywords/allow",
            review_search_allow,
            methods=["GET", "POST", "DELETE"],
        ),
        Route(
            "/review/search-keywords/block",
            review_search_block,
            methods=["GET", "POST", "DELETE"],
        ),
        Route("/review/activity", review_activity, methods=["GET"]),
        Route(
            "/review/activity/suggest-rule",
            review_activity_suggest_rule,
            methods=["POST"],
        ),
        Route(
            "/review/activity/suggest-rules",
            review_activity_suggest_rules,
            methods=["POST"],
        ),
        Route(
            "/review/activity/summary",
            review_activity_summary,
            methods=["GET", "POST"],
        ),
        Route(
            "/review/activity/summaries",
            review_activity_summaries,
            methods=["GET"],
        ),
        Route("/review/prompt", review_prompt, methods=["GET", "POST"]),
    ]
