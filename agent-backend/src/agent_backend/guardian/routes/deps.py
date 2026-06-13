"""Shared dependency bundle passed to every route builder.

``GuardianDeps`` is a frozen dataclass holding all runtime state that route
handlers need.  Each route module's ``build_routes(deps)`` defines its handlers
as closures capturing ``deps``, mirroring the original closure-inside-create_app
pattern but decoupled from the monolithic ``service.py`` scope.
"""
from __future__ import annotations

import hmac
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from ..classifier import Classifier
    from ..config import GuardianConfig
    from ..event_log import EventLog
    from ..metrics import GuardianMetrics
    from ..pin_store import PinStore
    from ..profile_manager import ProfileManager
    from ..runtime import ProfileRuntime
    from ..service import ExtPacker
    from ..time_ledger import TimeLedger


@dataclass(frozen=True, slots=True)
class GuardianDeps:
    """Bundle of shared runtime state injected into every route builder."""

    config: GuardianConfig
    classifier: Classifier
    pin_store: PinStore
    event_log: EventLog
    summary_log: EventLog
    metrics: GuardianMetrics
    pm: ProfileManager
    time_ledger: TimeLedger
    packer: ExtPacker
    repo_root: Path


def make_auth_helpers(
    deps: GuardianDeps,
) -> tuple[
    Callable[[Request], ProfileRuntime | None],
    Callable[[Request], JSONResponse | None],
]:
    """Return ``(resolve_runtime, require_pin)`` closures bound to *deps*.

    Mirrors the ``_resolve_runtime`` / ``_require_pin`` closures defined inside
    ``create_app`` in the original ``service.py``.
    """

    def resolve_runtime(request: Request) -> ProfileRuntime | None:
        token = request.headers.get("X-Guardian-Token", "")
        if not token:
            return None
        match: ProfileRuntime | None = None
        for runtime in deps.pm.snapshot().values():
            if hmac.compare_digest(runtime.token, token):
                match = runtime
        return match

    def require_pin(request: Request) -> JSONResponse | None:
        if not deps.pin_store.is_configured():
            return JSONResponse({"error": "parent PIN not configured"}, status_code=503)
        if not deps.pin_store.verify(request.headers.get("X-Guardian-Parent-Pin", "")):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return None

    return resolve_runtime, require_pin
