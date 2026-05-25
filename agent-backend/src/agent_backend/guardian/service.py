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
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..config import ConfigError
from .access_requests import RequestStore
from .blocklist import BlocklistStore
from .cache import VerdictCache
from .classifier import Classifier
from .config import GuardianConfig
from .event_log import EventLog
from .metrics import GuardianMetrics
from .normalize import extract_host, normalize_url
from .pin_store import PinStore, validate_pin_format
from .profile_manager import (
    InvalidProfileNameError,
    ProfileExistsError,
    ProfileManager,
    ProfileNotFoundError,
)
from .profiles import DEFAULT_PROFILE_NAME, ProfileRegistry
from .runtime import ProfileRuntime, build_runtime
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


def _build_runtimes(
    config: GuardianConfig,
    registry: ProfileRegistry | None,
    runtimes: dict[str, ProfileRuntime] | None,
    *,
    cache: VerdictCache | None,
    whitelist: WhitelistStore | None,
    request_store: RequestStore | None,
) -> dict[str, ProfileRuntime]:
    """Resolve per-profile stores. Precedence: injected runtimes > registry > single default.

    The single-default branch wraps the injected (or config-path) stores, so existing
    single-profile callers and tests are byte-identical.
    """
    if runtimes is not None:
        return runtimes
    if registry is not None:
        # build_runtime creates each teen's data dirs (incl. blocklist) and opens its stores.
        return {profile.name: build_runtime(profile) for profile in registry.all()}
    if not config.token:
        # Guard the public create_app() against a silent lockout: an empty default token
        # matches no request. (__main__ resolves the registry first, which also checks this.)
        raise ConfigError(
            "create_app: no registry/runtimes and GUARDIAN_TOKEN is empty — nothing could "
            "authenticate. Set GUARDIAN_TOKEN or pass a registry/runtimes."
        )
    default = ProfileRuntime(
        name=DEFAULT_PROFILE_NAME,
        token=config.token,
        whitelist=whitelist or WhitelistStore(config.whitelist_path),
        blocklist=BlocklistStore(config.blocklist_path),
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
    runtimes: dict[str, ProfileRuntime] | None = None,
    pin_store: PinStore | None = None,
    manager: ProfileManager | None = None,
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
    # The PIN lives in the store (hash file written by /setup), with the env PIN as fallback,
    # so a PIN created at runtime takes effect without restarting (config is frozen at startup).
    pin_store = pin_store or PinStore(config.admin_path, env_pin=config.parent_pin)
    # The manager owns the live name->runtime map (read via _pm.snapshot()) and persists
    # profile lifecycle changes. When not injected, build it from the same runtimes the
    # service has always used, plus the registry's profile records (paths) for persistence.
    if manager is None:
        profile_runtimes = _build_runtimes(
            config,
            registry,
            runtimes,
            cache=cache,
            whitelist=whitelist,
            request_store=request_store,
        )
        profile_records = {p.name: p for p in registry.all()} if registry is not None else {}
        manager = ProfileManager(
            profile_records, profile_runtimes, profiles_path=config.profiles_path
        )
    _pm = manager

    def _resolve_runtime(request: Request) -> ProfileRuntime | None:
        """Map ``X-Guardian-Token`` to its teen profile, or None to reject.

        Compares against every profile's token with ``hmac.compare_digest`` (selecting after
        the loop, not on first hit) so a near-correct token can't be confirmed byte-by-byte by
        timing. An empty/absent token is rejected up front.
        """
        token = request.headers.get("X-Guardian-Token", "")
        if not token:
            return None
        match: ProfileRuntime | None = None
        for runtime in _pm.snapshot().values():
            if hmac.compare_digest(runtime.token, token):
                match = runtime
        return match

    def _require_pin(request: Request) -> JSONResponse | None:
        """Gate parent-only endpoints behind the parent PIN (never sent to the extension).

        Reads through ``pin_store`` (hash file, else env PIN), so a PIN created via /setup
        applies immediately. ``verify`` is constant-time.
        """
        if not pin_store.is_configured():
            return JSONResponse({"error": "parent PIN not configured"}, status_code=503)
        if not pin_store.verify(request.headers.get("X-Guardian-Parent-Pin", "")):
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

    async def home_page(_request: Request) -> Response:
        # The parent app shell. On first run there is no PIN and nothing to show, so route to
        # the setup wizard. The redirect is server-side so it holds even with JS disabled.
        if not pin_store.is_configured():
            return RedirectResponse("/setup", status_code=302)
        return FileResponse(Path(__file__).parent / "home.html", media_type="text/html")

    async def setup_page(_request: Request) -> Response:
        # First-run wizard. No auth: there is no PIN/token to present yet (like /health).
        # Once a PIN exists there is nothing to set up — send the parent to the shell.
        if pin_store.is_configured():
            return RedirectResponse("/", status_code=302)
        return FileResponse(Path(__file__).parent / "setup.html", media_type="text/html")

    async def setup_status(_request: Request) -> JSONResponse:
        # Lets the wizard detect first run on load. Leaks only whether a PIN exists, nothing else.
        return JSONResponse({"pin_configured": pin_store.is_configured()})

    async def setup_pin(request: Request) -> JSONResponse:
        # One-shot: once a PIN exists this is closed (409), so it can't reset an existing PIN.
        if pin_store.is_configured():
            return JSONResponse({"error": "parent PIN already configured"}, status_code=409)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        pin = str(body.get("pin", "")).strip()
        error = validate_pin_format(pin)
        if error is not None:
            return JSONResponse({"error": error}, status_code=422)
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, pin_store.set_pin, pin)
        except OSError:
            return JSONResponse({"error": "could not save the PIN"}, status_code=500)
        event_log.log("parent_pin_set")  # records the event, never the PIN value
        return JSONResponse({"ok": True})

    async def review_page(_request: Request) -> RedirectResponse:
        # Folded into the app shell: keep the /review bookmark working by routing into the
        # Requests section. The "#/requests" fragment is read client-side; the server sees "/".
        return RedirectResponse("/#/requests", status_code=302)

    async def review_requests(request: Request) -> JSONResponse:
        guard = _require_pin(request)
        if guard is not None:
            return guard
        # One parent reviews every teen: aggregate across profiles, labelling each request
        # with the teen it belongs to so approvals can be routed back to the right whitelist.
        pending: list[dict[str, Any]] = []
        recent: list[dict[str, Any]] = []
        for runtime in _pm.snapshot().values():
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
        owner: ProfileRuntime | None = None
        existing = None
        for runtime in _pm.snapshot().values():
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

    def _resolve_parent_profile(name: str) -> ProfileRuntime | None:
        """Pick the profile a parent whitelist write targets.

        Parents authenticate with the PIN (not a teen token), so the request carries no
        profile. Use the explicitly named profile, else the sole profile when there is only
        one teen (the common case); an ambiguous multi-teen write must name one.
        """
        current = _pm.snapshot()
        if name:
            return current.get(name)
        if len(current) == 1:
            return next(iter(current.values()))
        return None

    async def review_whitelist(request: Request) -> JSONResponse:
        # Parent-facing whitelist management, gated by the PIN (the teen-token /whitelist is the
        # extension's path). GET aggregates across teens; a write targets one teen's store.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        if request.method == "GET":
            entries = [
                {"value": value, "type": classify_entry(value), "profile": rt.name}
                for rt in _pm.snapshot().values()
                for value in rt.whitelist.current().values
            ]
            return JSONResponse({"entries": entries})
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        entry = str(body.get("entry", "")).strip()
        # Same guard as POST /whitelist: a single bounded line (content entries are injected
        # verbatim into the classifier prompt, so reject newlines / control chars).
        if not entry or len(entry) > 512 or not entry.isprintable():
            return JSONResponse(
                {"error": "entry must be a non-empty, single-line string (max 512 chars)"},
                status_code=422,
            )
        rt = _resolve_parent_profile(str(body.get("profile", "")).strip())
        if rt is None:
            return JSONResponse({"error": "profile required"}, status_code=422)
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
        event_log.log(f"parent_whitelist_{request.method.lower()}", entry=entry, profile=rt.name)
        if request.method == "POST":
            return JSONResponse({"value": entry, "type": classify_entry(entry)})
        return JSONResponse({"ok": True})

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

    def _profile_config(token: str) -> dict[str, str]:
        """The extension's guardian-config.json contents for a profile's token."""
        return {"token": token, "endpoint": f"http://{config.host}:{config.port}"}

    async def profiles_endpoint(request: Request) -> JSONResponse:
        # Parent-only profile management. GET lists profiles (never their tokens); POST creates
        # one and returns its freshly generated token + a ready-to-paste extension config ONCE
        # (the UI shows it then forgets it -- it is never re-fetchable).
        guard = _require_pin(request)
        if guard is not None:
            return guard
        if request.method == "GET":
            return JSONResponse({"profiles": _pm.list_profiles()})
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        loop = asyncio.get_running_loop()
        try:
            runtime, token = await loop.run_in_executor(None, _pm.create, str(body.get("name", "")))
        except InvalidProfileNameError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except ProfileExistsError:
            return JSONResponse(
                {"error": "a profile with that name already exists"}, status_code=409
            )
        except (ConfigError, OSError):
            return JSONResponse({"error": "could not create profile data"}, status_code=500)
        event_log.log("profile_created", profile=runtime.name)  # never the token
        return JSONResponse(
            {"name": runtime.name, "token": token, "config": _profile_config(token)},
            status_code=201,
        )

    async def profile_rename(request: Request) -> JSONResponse:
        guard = _require_pin(request)
        if guard is not None:
            return guard
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        name = request.path_params["name"]
        new_name = str(body.get("new_name", ""))
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, functools.partial(_pm.rename, name, new_name))
        except InvalidProfileNameError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except ProfileNotFoundError:
            return JSONResponse({"error": "profile not found"}, status_code=404)
        except ProfileExistsError:
            return JSONResponse(
                {"error": "a profile with that name already exists"}, status_code=409
            )
        except (ConfigError, OSError):
            return JSONResponse({"error": "could not rename profile"}, status_code=500)
        event_log.log("profile_renamed", profile=new_name.strip())
        return JSONResponse({"ok": True})

    async def profile_regenerate_token(request: Request) -> JSONResponse:
        guard = _require_pin(request)
        if guard is not None:
            return guard
        name = request.path_params["name"]
        loop = asyncio.get_running_loop()
        try:
            token = await loop.run_in_executor(None, _pm.regenerate_token, name)
        except ProfileNotFoundError:
            return JSONResponse({"error": "profile not found"}, status_code=404)
        except (ConfigError, OSError):
            return JSONResponse({"error": "could not regenerate token"}, status_code=500)
        event_log.log("profile_token_regenerated", profile=name)  # never the token
        return JSONResponse({"token": token, "config": _profile_config(token)})

    async def profile_delete(request: Request) -> JSONResponse:
        guard = _require_pin(request)
        if guard is not None:
            return guard
        name = request.path_params["name"]
        purge = request.query_params.get("purge", "").lower() in ("1", "true", "yes")
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, functools.partial(_pm.delete, name, purge=purge)
            )
        except ProfileNotFoundError:
            return JSONResponse({"error": "profile not found"}, status_code=404)
        event_log.log("profile_deleted", profile=name, purged=purge)
        return JSONResponse({"ok": True})

    app = Starlette(
        routes=[
            Route("/", home_page, methods=["GET"]),
            Route("/health", health),
            Route("/classify", classify, methods=["POST"]),
            Route("/dwell", dwell, methods=["POST"]),
            Route("/whitelist", whitelist_endpoint, methods=["GET", "POST", "DELETE"]),
            Route("/access-request", access_request_endpoint, methods=["GET", "POST"]),
            Route("/setup", setup_page, methods=["GET"]),
            Route("/setup/status", setup_status, methods=["GET"]),
            Route("/setup/pin", setup_pin, methods=["POST"]),
            Route("/review", review_page, methods=["GET"]),
            Route("/review/requests", review_requests, methods=["GET"]),
            Route("/review/decision", review_decision, methods=["POST"]),
            Route("/review/whitelist", review_whitelist, methods=["GET", "POST", "DELETE"]),
            Route("/settings/pin", settings_change_pin, methods=["POST"]),
            Route("/profiles", profiles_endpoint, methods=["GET", "POST"]),
            # More-specific paths first so {name} doesn't swallow /rename and /token.
            Route("/profiles/{name}/rename", profile_rename, methods=["POST"]),
            Route("/profiles/{name}/token", profile_regenerate_token, methods=["POST"]),
            Route("/profiles/{name}", profile_delete, methods=["DELETE"]),
            # Shared design-system assets (tokens, component CSS, self-hosted fonts, brand
            # SVG) for the served pages. No auth — purely static, public styling like the
            # page shells themselves. The /static prefix never shadows the exact routes above.
            Mount(
                "/static",
                app=StaticFiles(directory=Path(__file__).parent / "static"),
                name="static",
            ),
        ]
    )
    app.state.config = config
    return app
