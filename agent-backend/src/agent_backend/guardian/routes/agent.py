"""Routes: /health, /version, /agent/chat, /agent/apply, /settings/pin.

Module-level helpers (constants, parsers, prompt builders) are pure functions so they can be
tested without a live app; route handlers are closures over ``GuardianDeps`` returned by
``build_routes()``.
"""
from __future__ import annotations

import asyncio
import functools
import json
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..normalize import extract_host
from ..pin_store import validate_pin_format
from ..profiles import GLOBAL_PROFILE_NAME
from ..service import (
    _MAX_PRIZE_POINTS,
    _MAX_PROMPT_CHARS,
    _MAX_TIME_TEXT,
    _SUMMARY_OBJECT_RE,
    _TIME_POLICY_SYSTEM_PROMPT,
    ACTIVITY_EVENTS,
    _activity_digest,
    _clamp_prize_points,
    _clean_str_list,
    _stack_versions,
    _valid_prompt_text,
)
from ..time_policy import parse_policy as parse_time_policy
from ..time_policy import to_json as time_policy_to_json
from .deps import GuardianDeps, make_auth_helpers

if TYPE_CHECKING:
    from ..runtime import ProfileRuntime

# ---------------------------------------------------------------------------
# The agent's entire write surface.  Destructive ops (PIN change, profile
# delete/rename, token regen) are deliberately absent — the LLM must never be
# able to even propose them through this channel.
# ---------------------------------------------------------------------------
_AGENT_APPLY_ACTIONS: frozenset[str] = frozenset(
    {
        "whitelist.add",
        "whitelist.remove",
        "blocklist.add",
        "blocklist.remove",
        "search_allow.add",
        "search_allow.remove",
        "search_block.add",
        "search_block.remove",
        "time_policy.set",
        "prize.grant",
        "prompt.set",
    }
)

_AGENT_HISTORY_MAX = 20  # conversation turns kept (server-enforced cap on token cost)
_AGENT_MSG_MAX_CHARS = 4000  # per-message clamp
_AGENT_REPLY_MAX = 8000  # reply clamp
_AGENT_MAX_PROPOSALS = 5
_AGENT_MAX_SUGGESTIONS = 6
_AGENT_RATIONALE_MAX = 300
_AGENT_SUGGESTION_MAX = 80
_AGENT_LIST_CAP = 50  # config-list entries shown per profile
_AGENT_ACTIVITY_LIMIT = 150  # events pulled for the data slice
_AGENT_ACTIVITY_MAX_LINES = 80  # rendered activity lines


class _AgentApplyError(Exception):
    """A controlled failure while applying an agent-proposed change → HTTP status + message."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


_AGENT_SYSTEM_HEADER = (
    "You are the Aegis Guardian assistant — the AI agent inside a parental-control browser "
    "system. A parent ('guardian') is talking to you in their dashboard. Their child uses a "
    "locked Chromium browser whose extension classifies and blocks pages through this guardian "
    "backend. Per-profile rules control allowed/blocked sites, allowed/blocked search keywords, "
    "daily screen-time budgets and bedtime, prize points (kids redeem them for bonus screen "
    "time), and a custom classification prompt.\n\n"
    "You can: answer questions about how the system works; analyze the configuration and recent "
    "activity shown below; and PROPOSE configuration changes for the parent to approve. You never "
    "change anything yourself — you propose, and the parent applies.\n\n"
    "Respond with ONLY a single JSON object (no prose outside it, no code fences):\n"
    '{"reply": "<markdown answer for the parent>", '
    '"proposals": [{"action": "<key>", "profile": "<profile name or null>", '
    '"params": {...}, "rationale": "<one sentence>"}], '
    '"suggestions": ["<short follow-up the parent might tap>"]}\n\n'
    "Propose an action ONLY when the parent clearly wants a change. Allowed actions and params:\n"
    "- whitelist.add / whitelist.remove {entry}: allow / un-allow a site or topic\n"
    "- blocklist.add / blocklist.remove {entry}: block / unblock a site or topic\n"
    "- search_allow.add / search_allow.remove {entry}: allow / un-allow a search keyword\n"
    "- search_block.add / search_block.remove {entry}: block / unblock a search keyword\n"
    "- time_policy.set {text}: set screen-time rules from a natural-language description\n"
    "- prize.grant {points, reason}: award (negative deducts) prize points to a child\n"
    "- prompt.set {prompt}: replace a profile's custom classification prompt\n"
    "'profile' is one of the profile names listed below, or 'global' for the shared rules that "
    "apply to every child (prize.grant must name a specific child, never global). Keep 'reply' "
    "concise and warm. Use empty lists when there is nothing to propose or suggest."
)


def _coerce_chat_messages(value: object) -> list[dict[str, str]]:
    """Validate conversation history → clean ``{role, content}`` list, bounded to the last N.

    Non-list input, non-dict entries, unknown roles, and non-string/blank content are dropped;
    each message is clamped and only the most recent ``_AGENT_HISTORY_MAX`` are kept (a
    server-enforced cap so a long client history can't blow up the prompt).
    """
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip()
        if role not in ("user", "assistant"):
            continue
        content = item.get("content")
        if not isinstance(content, str):
            continue
        text = content.strip()[:_AGENT_MSG_MAX_CHARS]
        if text:
            out.append({"role": role, "content": text})
    return out[-_AGENT_HISTORY_MAX:]


def _render_conversation(messages: list[dict[str, str]]) -> str:
    """Render the bounded history as a labeled transcript for the user prompt."""
    labels = {"user": "Guardian", "assistant": "Assistant"}
    return "\n\n".join(f"{labels[m['role']]}: {m['content']}" for m in messages)


def _parse_agent_proposals(value: object) -> list[dict[str, Any]]:
    """Coerce model proposals → bounded list of valid, known-action entries (unknown dropped)."""
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).strip()
        if action not in _AGENT_APPLY_ACTIONS:  # unknown / destructive → dropped
            continue
        params = item.get("params")
        profile_val = item.get("profile")
        out.append(
            {
                "action": action,
                "profile": (
                    profile_val.strip()
                    if isinstance(profile_val, str) and profile_val.strip()
                    else None
                ),
                "params": params if isinstance(params, dict) else {},
                "rationale": str(item.get("rationale", "")).strip()[:_AGENT_RATIONALE_MAX],
            }
        )
        if len(out) >= _AGENT_MAX_PROPOSALS:
            break
    return out


def _parse_agent_response(raw: str) -> dict[str, Any]:
    """Best-effort extraction of ``{reply, proposals[], suggestions[]}`` (never raises).

    Mirrors ``_parse_activity_summary``: any non-JSON / malformed output degrades to a plain
    ``reply`` carrying the whole text, so a confused model can't 500 the endpoint.
    """
    fallback = raw.strip()[:_AGENT_REPLY_MAX] if raw else ""
    empty: dict[str, Any] = {"reply": fallback, "proposals": [], "suggestions": []}
    if not raw:
        return empty
    text = raw.strip()
    candidates = [text]
    match = _SUMMARY_OBJECT_RE.search(text)
    if match is not None:
        candidates.append(match.group(0))
    data: object = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            break
    if not isinstance(data, dict):
        return empty
    reply = str(data.get("reply", "")).strip()[:_AGENT_REPLY_MAX] or fallback
    return {
        "reply": reply,
        "proposals": _parse_agent_proposals(data.get("proposals")),
        "suggestions": _clean_str_list(
            data.get("suggestions"),
            max_len=_AGENT_SUGGESTION_MAX,
            max_items=_AGENT_MAX_SUGGESTIONS,
        ),
    }


def _agent_list_preview(values: object, *, cap: int = _AGENT_LIST_CAP) -> str:
    """Comma-join a config list for the prompt, capped with a '[+N more]' suffix."""
    vals = [str(v) for v in values] if isinstance(values, (list, tuple)) else []
    if not vals:
        return "(none)"
    suffix = f" [+{len(vals) - cap} more]" if len(vals) > cap else ""
    return ", ".join(vals[:cap]) + suffix


def _agent_time_policy_line(rt: ProfileRuntime) -> str:
    """A one-line, human-readable summary of a profile's screen-time policy."""
    policy = rt.time_policy.current()
    if not policy.is_set():
        return "(no screen-time limits set)"
    if policy.source_text.strip():
        return policy.source_text.strip()[:300]
    default = policy.daily_minutes.get("default")
    return f"daily budget {default} min" if default is not None else "(custom limits set)"


def _agent_config_snapshot(runtimes: list[ProfileRuntime]) -> str:
    """Render the per-profile configuration snapshot injected into the system prompt."""
    blocks: list[str] = []
    for rt in runtimes:
        prompt_text = rt.prompt_store.current().strip()
        prompt_line = (
            (prompt_text[:200] + "…" if len(prompt_text) > 200 else prompt_text)
            if prompt_text
            else "(age-band default)"
        )
        blocks.append(
            f"Profile: {rt.name} (age {rt.age})\n"
            f"  Allowed sites (whitelist): {_agent_list_preview(rt.whitelist.current().values)}\n"
            f"  Blocked sites (blocklist): {_agent_list_preview(rt.blocklist.current().values)}\n"
            f"  Allowed search keywords: {_agent_list_preview(rt.search_allow.current().values)}\n"
            f"  Blocked search keywords: {_agent_list_preview(rt.search_block.current().values)}\n"
            f"  Screen-time policy: {_agent_time_policy_line(rt)}\n"
            f"  Prize balance: {rt.prize_point_store.balance()} points\n"
            f"  Custom classification prompt: {prompt_line}"
        )
    return "\n\n".join(blocks)


def _agent_activity_overview(events: list[dict[str, Any]]) -> str:
    """Compact verdict tally + top hosts across the activity slice."""
    verdicts: Counter[str] = Counter()
    hosts: Counter[str] = Counter()
    for ev in events:
        verdicts[str(ev.get("event") or "?")] += 1
        url = str(ev.get("url") or ev.get("url_key") or "")
        host = extract_host(url) if url else ""
        if host:
            hosts[host] += 1
    vparts = ", ".join(f"{k}={n}" for k, n in verdicts.most_common())
    hparts = ", ".join(f"{h}({n})" for h, n in hosts.most_common(10))
    return f"Verdict totals: {vparts or '(none)'}\nTop hosts: {hparts or '(none)'}"


def _agent_activity_text(events: list[dict[str, Any]], ages: dict[str, int]) -> str:
    """The activity section: a verdict/host overview plus the per-profile event digest."""
    if not events:
        return "(no recent activity)"
    overview = _agent_activity_overview(events)
    digest = _activity_digest(events, ages, max_lines=_AGENT_ACTIVITY_MAX_LINES)
    return f"{overview}\n\n{digest}"


def _build_agent_system_prompt(
    runtimes: list[ProfileRuntime], activity_text: str, versions: dict[str, Any]
) -> str:
    """Assemble the full system prompt: static header + stack + config snapshot + activity."""
    version_line = (
        f"Guardian v{versions.get('guardian')} | Extension v{versions.get('extension')} | "
        f"Model: {versions.get('model')}"
    )
    return (
        f"{_AGENT_SYSTEM_HEADER}\n\n"
        f"=== STACK ===\n{version_line}\n\n"
        f"=== CURRENT CONFIGURATION ===\n{_agent_config_snapshot(runtimes)}\n\n"
        f"=== RECENT ACTIVITY ===\n{activity_text}"
    )


# ---------------------------------------------------------------------------
# Route factory
# ---------------------------------------------------------------------------


def build_routes(deps: GuardianDeps) -> list[Route]:
    """Return health, version, agent-chat/apply, and settings/pin routes bound to *deps*."""
    _, _require_pin = make_auth_helpers(deps)
    event_log = deps.event_log
    classifier = deps.classifier
    config = deps.config
    metrics = deps.metrics
    pin_store = deps.pin_store

    def _resolve_parent_profile(name: str) -> ProfileRuntime | None:
        if name == GLOBAL_PROFILE_NAME:
            return deps.pm.global_runtime()
        current = deps.pm.snapshot()
        if name:
            return current.get(name)
        if len(current) == 1:
            return next(iter(current.values()))
        return None

    async def _clear_caches_after_list_change(rt: ProfileRuntime) -> None:
        loop = asyncio.get_running_loop()
        targets = (
            list(deps.pm.snapshot().values()) if rt.name == GLOBAL_PROFILE_NAME else [rt]
        )
        for target in targets:
            await loop.run_in_executor(None, target.cache.clear)

    def _resolve_apply_profile(profile_str: str, *, teen_only: bool = False) -> ProfileRuntime:
        """Resolve the profile an apply targets, raising ``_AgentApplyError`` on failure.

        ``teen_only`` (prize grants) requires a named child and never resolves Global; otherwise
        ``"global"`` / a teen name / the sole teen (empty) are accepted, mirroring the typed
        parent endpoints.
        """
        if teen_only:
            owner = deps.pm.snapshot().get(profile_str)
            if owner is None:
                raise _AgentApplyError(
                    404 if profile_str else 422,
                    "unknown profile" if profile_str else "a specific child profile is required",
                )
            return owner
        rt = _resolve_parent_profile(profile_str)
        if rt is None:
            raise _AgentApplyError(
                404 if profile_str else 422,
                "unknown profile" if profile_str else "profile required",
            )
        return rt

    def _make_list_apply(
        store_attr: str, verb: str, kind: str
    ) -> Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]:
        """Build an add/remove dispatcher for a per-profile entry list."""

        async def _dispatch(profile_str: str, params: dict[str, Any]) -> dict[str, Any]:
            entry = str(params.get("entry", "")).strip()
            if not entry or len(entry) > 512 or not entry.isprintable():
                raise _AgentApplyError(
                    422, "entry must be a non-empty, single-line string (max 512 chars)"
                )
            rt = _resolve_apply_profile(profile_str)
            store = getattr(rt, store_attr)
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(
                    None, store.add if verb == "add" else store.remove, entry
                )
                await _clear_caches_after_list_change(rt)
            except OSError as exc:
                raise _AgentApplyError(500, f"{kind} write failed") from exc
            event_log.log(f"agent_{kind}_{verb}", entry=entry, profile=rt.name)
            return {"profile": rt.name, "entry": entry}

        return _dispatch

    async def _apply_prize_grant(profile_str: str, params: dict[str, Any]) -> dict[str, Any]:
        points = _clamp_prize_points(params.get("points"))
        if points is None:
            raise _AgentApplyError(
                422, f"points must be a non-zero integer within +-{_MAX_PRIZE_POINTS}"
            )
        owner = _resolve_apply_profile(profile_str, teen_only=True)
        raw_reason = params.get("reason")
        reason = raw_reason.strip()[:200] if isinstance(raw_reason, str) else ""
        loop = asyncio.get_running_loop()
        try:
            balance = await loop.run_in_executor(None, owner.prize_point_store.add, points)
        except OSError as exc:
            raise _AgentApplyError(500, "prize write failed") from exc
        event_log.log(
            "prize_points_earned",
            profile=owner.name,
            delta=points,
            reason=reason,
            balance_after=balance,
        )
        metrics.record_prize_grant(owner.name, points, balance)
        return {"profile": owner.name, "points": points, "balance": balance}

    async def _apply_prompt_set(profile_str: str, params: dict[str, Any]) -> dict[str, Any]:
        prompt = str(params.get("prompt", ""))
        if not _valid_prompt_text(prompt):
            raise _AgentApplyError(
                422, f"prompt must be printable text up to {_MAX_PROMPT_CHARS} chars"
            )
        rt = _resolve_apply_profile(profile_str)
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, rt.prompt_store.set, prompt)
            await _clear_caches_after_list_change(rt)
        except OSError as exc:
            raise _AgentApplyError(500, "prompt write failed") from exc
        event_log.log("agent_prompt_set", profile=rt.name, length=len(prompt))
        return {"profile": rt.name, "length": len(prompt)}

    async def _apply_time_policy_set(
        profile_str: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        text = str(params.get("text", "")).strip()
        if not text or len(text) > _MAX_TIME_TEXT:
            raise _AgentApplyError(422, f"text required (max {_MAX_TIME_TEXT} chars)")
        rt = _resolve_apply_profile(profile_str)
        # Convert the parent's natural-language limits into the structured policy with the same
        # prompt the /time/policy/parse preview uses, then save it (validated + clamped on parse).
        try:
            raw = await asyncio.wait_for(
                classifier.generate(system_prompt=_TIME_POLICY_SYSTEM_PROMPT, user_prompt=text),
                timeout=config.classify_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 - NL→policy conversion failed
            raise _AgentApplyError(502, "time policy parse failed") from exc
        policy = parse_time_policy(raw, source_text=text, updated_ts=datetime.now(UTC).isoformat())
        loop = asyncio.get_running_loop()
        try:
            saved = await loop.run_in_executor(None, rt.time_policy.set, policy)
        except OSError as exc:
            raise _AgentApplyError(500, "policy write failed") from exc
        event_log.log("time_policy_set", profile=rt.name)
        return {"profile": rt.name, "policy": time_policy_to_json(saved)}

    _apply_registry: dict[str, Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]] = {
        "whitelist.add": _make_list_apply("whitelist", "add", "whitelist"),
        "whitelist.remove": _make_list_apply("whitelist", "remove", "whitelist"),
        "blocklist.add": _make_list_apply("blocklist", "add", "blocklist"),
        "blocklist.remove": _make_list_apply("blocklist", "remove", "blocklist"),
        "search_allow.add": _make_list_apply("search_allow", "add", "search_allow"),
        "search_allow.remove": _make_list_apply("search_allow", "remove", "search_allow"),
        "search_block.add": _make_list_apply("search_block", "add", "search_block"),
        "search_block.remove": _make_list_apply("search_block", "remove", "search_block"),
        "time_policy.set": _apply_time_policy_set,
        "prize.grant": _apply_prize_grant,
        "prompt.set": _apply_prompt_set,
    }
    if set(_apply_registry) != _AGENT_APPLY_ACTIONS:  # keep the write surface defined in one place
        raise RuntimeError("agent apply registry out of sync with _AGENT_APPLY_ACTIONS")

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def version_endpoint(request: Request) -> JSONResponse:
        """Return the deployed stack versions (parent-only; also fed to the Agent's context)."""
        guard = _require_pin(request)
        if guard is not None:
            return guard
        return JSONResponse(_stack_versions(config.agent_model))

    async def agent_chat(request: Request) -> JSONResponse:
        """Conversational guardian assistant: assemble context, ask the model, return the envelope.

        Read-only over all stores — the model proposes config changes (applied via /agent/apply),
        never writes here. PIN-gated; parsing is fail-safe; the conversation is stateless
        server-side (the client re-sends the bounded history each turn).
        """
        guard = _require_pin(request)
        if guard is not None:
            return guard
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        messages = _coerce_chat_messages(body.get("messages"))
        if not messages or messages[-1]["role"] != "user":
            return JSONResponse(
                {"error": "messages must be a non-empty list ending with a user message"},
                status_code=422,
            )
        # ``profile`` may arrive as a name, "", or JSON null (the "all profiles" scope the UI
        # sends). Normalize anything non-string — including null — to None; never str(None),
        # which would become the literal "None" and 404 as an unknown profile.
        profile_raw = body.get("profile")
        profile_name = (
            profile_raw.strip() if isinstance(profile_raw, str) and profile_raw.strip() else None
        )
        snap = deps.pm.snapshot()
        if profile_name is not None and profile_name not in snap:
            return JSONResponse({"error": "unknown profile"}, status_code=404)
        if profile_name is not None:
            runtimes = [snap[profile_name], deps.pm.global_runtime()]
        else:
            runtimes = [*snap.values(), deps.pm.global_runtime()]
        loop = asyncio.get_running_loop()
        events = await loop.run_in_executor(
            None,
            functools.partial(
                event_log.recent,
                _AGENT_ACTIVITY_LIMIT,
                profile=profile_name,
                events=ACTIVITY_EVENTS,
            ),
        )
        ages = {rt.name: rt.age for rt in snap.values()}
        system_prompt = _build_agent_system_prompt(
            runtimes,
            _agent_activity_text(events, ages),
            _stack_versions(config.agent_model),
        )
        try:
            raw = await asyncio.wait_for(
                classifier.generate(
                    system_prompt=system_prompt,
                    user_prompt=_render_conversation(messages),
                    model=config.agent_model,
                ),
                timeout=config.classify_timeout_s,
            )
        except Exception:  # noqa: BLE001 - any SDK/transport/timeout error → friendly 502
            return JSONResponse({"error": "agent chat failed"}, status_code=502)
        result = _parse_agent_response(raw)
        event_log.log("agent_chat", profile=profile_name or "", proposals=len(result["proposals"]))
        return JSONResponse(result)

    async def agent_apply(request: Request) -> JSONResponse:
        """Apply ONE parent-approved config change the agent proposed (PIN-gated, one per call).

        The action must be in the allow-listed registry (destructive ops are absent by design);
        the matching dispatcher re-validates params server-side before any write.
        """
        guard = _require_pin(request)
        if guard is not None:
            return guard
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        action = str(body.get("action", "")).strip()
        dispatch = _apply_registry.get(action)
        if dispatch is None:
            return JSONResponse({"error": "unknown or unsupported action"}, status_code=422)
        params = body.get("params")
        if not isinstance(params, dict):
            return JSONResponse({"error": "params must be an object"}, status_code=422)
        # Normalize null / non-string to "" (→ resolves to the sole teen, or 422). Never str(None).
        profile_raw = body.get("profile")
        profile_str = profile_raw.strip() if isinstance(profile_raw, str) else ""
        try:
            result = await dispatch(profile_str, params)
        except _AgentApplyError as exc:
            return JSONResponse({"error": exc.message}, status_code=exc.status)
        event_log.log("agent_apply", action=action, profile=str(result.get("profile", "")))
        return JSONResponse({"ok": True, "action": action, "result": result})

    async def settings_change_pin(request: Request) -> JSONResponse:
        # Rotate the parent PIN. Re-authenticate with the *current* PIN from the body (not the
        # header) so an unlocked-but-unattended tab can't silently change the credential. We
        # verify the supplied current PIN ourselves, so this does not use _require_pin.
        if not pin_store.is_configured():
            return JSONResponse({"error": "parent PIN not configured"}, status_code=503)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        current = str(body.get("current_pin", "")).strip()
        new_pin = str(body.get("new_pin", "")).strip()
        if not pin_store.verify(current):
            return JSONResponse({"error": "current PIN is incorrect"}, status_code=403)
        error = validate_pin_format(new_pin)
        if error is not None:
            return JSONResponse({"error": error}, status_code=400)
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, pin_store.set_pin, new_pin)
        except OSError:
            return JSONResponse({"error": "could not save the PIN"}, status_code=500)
        event_log.log("parent_pin_changed")  # records the event, never the PIN value
        return JSONResponse({"ok": True})

    return [
        Route("/health", health),
        Route("/version", version_endpoint, methods=["GET"]),
        Route("/agent/chat", agent_chat, methods=["POST"]),
        Route("/agent/apply", agent_apply, methods=["POST"]),
        Route("/settings/pin", settings_change_pin, methods=["POST"]),
    ]
