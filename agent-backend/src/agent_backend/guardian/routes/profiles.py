"""Routes: /profiles, /profiles/{name}/rename, /profiles/{name}/token, /profiles/{name}."""

from __future__ import annotations

import asyncio
import functools

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ...config import ConfigError
from ..profile_manager import (
    InvalidProfileNameError,
    ProfileExistsError,
    ProfileNotFoundError,
)
from .deps import GuardianDeps, make_auth_helpers


def build_routes(deps: GuardianDeps) -> list[Route]:
    """Return profile CRUD routes bound to *deps*."""
    _, _require_pin = make_auth_helpers(deps)
    pm = deps.pm
    event_log = deps.event_log
    config = deps.config

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
            return JSONResponse({"profiles": pm.list_profiles()})
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid JSON body"}, status_code=422)
        loop = asyncio.get_running_loop()
        try:
            runtime, token = await loop.run_in_executor(None, pm.create, str(body.get("name", "")))
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
            await loop.run_in_executor(None, functools.partial(pm.rename, name, new_name))
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
            token = await loop.run_in_executor(None, pm.regenerate_token, name)
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
                None, functools.partial(pm.delete, name, purge=purge)
            )
        except ProfileNotFoundError:
            return JSONResponse({"error": "profile not found"}, status_code=404)
        event_log.log("profile_deleted", profile=name, purged=purge)
        return JSONResponse({"ok": True})

    return [
        Route("/profiles", profiles_endpoint, methods=["GET", "POST"]),
        Route("/profiles/{name}/rename", profile_rename, methods=["POST"]),
        Route("/profiles/{name}/token", profile_regenerate_token, methods=["POST"]),
        Route("/profiles/{name}", profile_delete, methods=["DELETE"]),
    ]
