"""Routes: GET /, GET /setup, GET /setup/status, POST /setup/pin, GET /setup/health, GET /review."""
from __future__ import annotations

import asyncio
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from ..pin_store import validate_pin_format
from ..service import (
    _BACKEND_VERSION,
    _extension_dist_status,
    _is_loopback_client,
    _probe_network_firewall,
)
from .deps import GuardianDeps, make_auth_helpers

# The guardian package directory — where home.html / setup.html live.
# This file is at guardian/routes/setup.py so two .parent steps reach guardian/.
_GUARDIAN_DIR = Path(__file__).parent.parent


def build_routes(deps: GuardianDeps) -> list[Route]:
    """Return routes for /, /setup, /setup/status, /setup/pin, /setup/health, /review."""
    _, _require_pin = make_auth_helpers(deps)
    config = deps.config
    pin_store = deps.pin_store
    event_log = deps.event_log

    async def home_page(_request: Request) -> Response:
        # The parent app shell. On first run there is no PIN and nothing to show, so route to
        # the setup wizard. The redirect is server-side so it holds even with JS disabled.
        if not pin_store.is_configured():
            return RedirectResponse("/setup", status_code=302)
        return FileResponse(_GUARDIAN_DIR / "home.html", media_type="text/html")

    async def setup_page(_request: Request) -> Response:
        # First-run wizard. No auth: there is no PIN/token to present yet (like /health).
        # Once a PIN exists there is nothing to set up — send the parent to the shell.
        if pin_store.is_configured():
            return RedirectResponse("/", status_code=302)
        return FileResponse(_GUARDIAN_DIR / "setup.html", media_type="text/html")

    async def setup_status(_request: Request) -> JSONResponse:
        # Lets the wizard detect first run on load. Leaks only whether a PIN exists, nothing else.
        return JSONResponse({"pin_configured": pin_store.is_configured()})

    async def setup_pin(request: Request) -> JSONResponse:
        # One-shot: once a PIN exists this is closed (409), so it can't reset an existing PIN.
        if pin_store.is_configured():
            return JSONResponse({"error": "parent PIN already configured"}, status_code=409)
        # Loopback-only: on a LAN-bound guardian, any device could otherwise race the
        # parent to create the PIN and become the permanent "parent".
        if not _is_loopback_client(request):
            return JSONResponse(
                {"error": "first-run setup is only available on the guardian Mac itself"},
                status_code=403,
            )
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

    async def setup_health(request: Request) -> JSONResponse:
        # Friendly status for the setup wizard / devices console. During first run (no PIN yet) it
        # is open so the wizard can show readiness, like /setup/status; once a PIN exists it is
        # PIN-gated like the rest of the console. The payload carries no secrets.
        if pin_store.is_configured():
            guard = _require_pin(request)
            if guard is not None:
                return guard
        elif not _is_loopback_client(request):
            # Pre-PIN, non-local callers learn only that setup is pending — not the LAN
            # topology, model, or profile names (the wizard runs on the guardian Mac).
            return JSONResponse({"guardian": {"ok": True}, "pin_configured": False})
        kids = [p for p in deps.pm.list_profiles() if not p.get("is_global")]
        lan_ip, firewall = await asyncio.get_running_loop().run_in_executor(
            None, _probe_network_firewall
        )
        port = config.port
        return JSONResponse(
            {
                "guardian": {"ok": True, "version": _BACKEND_VERSION},
                "claude_token": {"present": bool(config.oauth_token)},
                "model": config.model,
                "network": {
                    "host": config.host,
                    "port": port,
                    "lan_ip": lan_ip,
                    "lan_url": f"http://{lan_ip}:{port}" if lan_ip else None,
                    "lan_bound": config.host in ("0.0.0.0", "::", ""),
                },
                "firewall": {"state": firewall},
                "extension": _extension_dist_status(config.ext_dist_dir),
                "profiles": {"count": len(kids), "names": [p["name"] for p in kids]},
                "pin_configured": pin_store.is_configured(),
            }
        )

    async def review_page(_request: Request) -> RedirectResponse:
        # Folded into the app shell: keep the /review bookmark working by routing into the
        # Requests section. The "#/requests" fragment is read client-side; the server sees "/".
        return RedirectResponse("/#/requests", status_code=302)

    return [
        Route("/", home_page, methods=["GET"]),
        Route("/setup", setup_page, methods=["GET"]),
        Route("/setup/status", setup_status, methods=["GET"]),
        Route("/setup/health", setup_health, methods=["GET"]),
        Route("/setup/pin", setup_pin, methods=["POST"]),
        Route("/review", review_page, methods=["GET"]),
    ]
