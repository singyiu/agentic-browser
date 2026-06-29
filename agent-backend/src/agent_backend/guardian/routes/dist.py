"""Routes: /ext/*, /dist/*, /enroll, /enroll/{profile}.

Extension and browser distribution endpoints — intentionally unauthenticated (Chrome's
extension updater and the kid bootstrapper can't present a token).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from ...config import ConfigError
from ..profile_manager import InvalidProfileNameError
from ..service import _SAFE_PROFILE_SEG, _probe_network_firewall, _render_kid_bootstrap
from .deps import GuardianDeps, make_auth_helpers


def build_routes(deps: GuardianDeps) -> list[Route]:
    """Return extension-dist, browser-dist, and enrollment routes bound to *deps*."""
    _, _require_pin = make_auth_helpers(deps)
    pm = deps.pm
    event_log = deps.event_log
    config = deps.config

    # --- self-hosted extension distribution (force-install via enterprise policy) ---
    # The kid browser's managed policy force-installs the parental-control extension from
    # these two routes. They are UNAUTHENTICATED on purpose: Chrome's extension updater
    # cannot present the X-Guardian-Token (same rationale as /static and /health). Both
    # serve fixed filenames from a configured dir — no path params, so no traversal risk —
    # and 404 until scripts/pack-extension.sh has produced the artifacts.
    def _ext_artifact(filename: str, media_type: str, *, profile: str | None = None) -> Response:
        base = Path(config.ext_dist_dir)
        if profile is not None:
            base = base / profile
        path = base / filename
        if not path.is_file():
            return Response("extension not packed", status_code=404, media_type="text/plain")
        return FileResponse(path, media_type=media_type)

    async def ext_updates(_request: Request) -> Response:
        return _ext_artifact("updates.xml", "text/xml")

    async def ext_crx(_request: Request) -> Response:
        return _ext_artifact("aegis.crx", "application/x-chrome-extension")

    # Per-profile artifacts (one packed CRX per kid, each with its own baked token + LAN endpoint).
    # The {profile} segment is validated against a strict charset so it cannot traverse out of the
    # dist dir. Each kid's managed policy points its update_url at /ext/<profile>/updates.xml.
    async def ext_updates_profile(request: Request) -> Response:
        profile = request.path_params["profile"]
        if not _SAFE_PROFILE_SEG.fullmatch(profile):
            return Response("not found", status_code=404, media_type="text/plain")
        return _ext_artifact("updates.xml", "text/xml", profile=profile)

    async def ext_crx_profile(request: Request) -> Response:
        profile = request.path_params["profile"]
        if not _SAFE_PROFILE_SEG.fullmatch(profile):
            return Response("not found", status_code=404, media_type="text/plain")
        return _ext_artifact("aegis.crx", "application/x-chrome-extension", profile=profile)

    # --- self-hosted browser distribution (the pre-built Chromium served to kid Macs) ---
    # Fixed filenames from the same dist dir as the extension; UNAUTHENTICATED on purpose: the
    # kid bootstrapper downloads the browser before it holds any token, and a browser binary is
    # not a secret. 404 until scripts/release-chromium.sh has published the artifacts.
    async def dist_manifest(_request: Request) -> Response:
        return _ext_artifact("chromium-manifest.json", "application/json")

    async def dist_browser(_request: Request) -> Response:
        return _ext_artifact("browser.zip", "application/zip")

    def _serve_repo_script(filename: str) -> Response:
        # Serve a self-contained kid-side script from the repo so a kid Mac (which has no repo) can
        # fetch it during setup/removal. Fixed names from a known dir — no params, no traversal.
        path = deps.repo_root / "agent-backend" / "scripts" / filename
        if not path.is_file():
            return Response("not available", status_code=404, media_type="text/plain")
        return FileResponse(path, media_type="text/x-shellscript")

    async def dist_kid_updater(_request: Request) -> Response:
        return _serve_repo_script("kid-update-check.sh")

    async def dist_kid_uninstaller(_request: Request) -> Response:
        return _serve_repo_script("uninstall-kid.sh")

    # --- per-kid enrollment ---
    # The default packer shells out to scripts/pack-extension.sh to build that kid's CRX (token +
    # LAN endpoint baked in) into {ext_dist_dir}/{profile}/. Tests inject a fake packer instead.
    async def enroll(request: Request) -> JSONResponse:
        # Parent-only: create (or reuse) the kid's profile, pack that kid's CRX (token + LAN
        # endpoint baked in), and return the one-time setup link to open on the kid Mac.
        guard = _require_pin(request)
        if guard is not None:
            return guard
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        name = str(body.get("name", "")).strip()
        loop = asyncio.get_running_loop()
        snap = pm.snapshot()
        if name in snap:
            runtime = snap[name]
            token = runtime.token
        else:
            try:
                runtime, token = await loop.run_in_executor(None, pm.create, name)
            except InvalidProfileNameError as exc:
                return JSONResponse({"error": str(exc)}, status_code=422)
            except (ConfigError, OSError):
                return JSONResponse({"error": "could not create profile data"}, status_code=500)
        lan_ip, _firewall = await loop.run_in_executor(None, _probe_network_firewall)
        endpoint = f"http://{lan_ip or config.host}:{config.port}"
        try:
            await deps.packer(runtime.name, token, endpoint)
        except Exception:  # noqa: BLE001 — packing the kid CRX is macOS-only and may be
            # unavailable on this guardian host (e.g. a Linux parent box). Don't lose the
            # just-created profile: return its token + config so the parent can set the kid
            # browser up by hand, and flag that the locked-browser package was not built.
            event_log.log("kid_enrolled", profile=runtime.name)  # never the token
            return JSONResponse(
                {
                    "profile": runtime.name,
                    "name": runtime.name,
                    "endpoint": endpoint,
                    "token": token,
                    "config": {"token": token, "endpoint": endpoint},
                    "packaged": False,
                },
                status_code=201,
            )
        event_log.log("kid_enrolled", profile=runtime.name)  # never the token
        return JSONResponse(
            {
                "profile": runtime.name,
                "endpoint": endpoint,
                "setup_url": f"{endpoint}/enroll/{runtime.name}",
                "update_url": f"{endpoint}/ext/{runtime.name}/updates.xml",
                "packaged": True,
            },
            status_code=201,
        )

    async def enroll_download(request: Request) -> Response:
        # Served to the KID Mac (unauthenticated — the kid has no PIN). Returns a double-clickable
        # .command that installs the locked browser for this profile. The {profile} segment is
        # charset-validated, and only our own endpoint/profile values are substituted in.
        profile = request.path_params["profile"]
        if not _SAFE_PROFILE_SEG.fullmatch(profile):
            return Response("not found", status_code=404, media_type="text/plain")
        lan_ip, _firewall = await asyncio.get_running_loop().run_in_executor(
            None, _probe_network_firewall
        )
        endpoint = f"http://{lan_ip or config.host}:{config.port}"
        try:
            script = _render_kid_bootstrap(endpoint, profile)
        except OSError:
            return Response("setup template unavailable", status_code=500, media_type="text/plain")
        return Response(
            script,
            media_type="text/x-shellscript",
            headers={"Content-Disposition": f'attachment; filename="Set up {profile}.command"'},
        )

    return [
        Route("/ext/updates.xml", ext_updates, methods=["GET"]),
        Route("/ext/aegis.crx", ext_crx, methods=["GET"]),
        Route("/ext/{profile}/updates.xml", ext_updates_profile, methods=["GET"]),
        Route("/ext/{profile}/aegis.crx", ext_crx_profile, methods=["GET"]),
        Route("/dist/manifest.json", dist_manifest, methods=["GET"]),
        Route("/dist/browser.zip", dist_browser, methods=["GET"]),
        Route("/dist/kid-update-check.sh", dist_kid_updater, methods=["GET"]),
        Route("/dist/uninstall-kid.sh", dist_kid_uninstaller, methods=["GET"]),
        Route("/enroll", enroll, methods=["POST"]),
        Route("/enroll/{profile}", enroll_download, methods=["GET"]),
    ]
