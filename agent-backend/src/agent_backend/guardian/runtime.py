"""The per-teen runtime: a profile's live, isolated stores.

Extracted into its own module so both the HTTP service and the profile manager can
depend on it without importing each other (which would be a circular import).
"""

from __future__ import annotations

from dataclasses import dataclass

from .access_requests import RequestStore
from .cache import VerdictCache
from .whitelist import WhitelistStore


@dataclass(frozen=True, slots=True)
class ProfileRuntime:
    """One teen's isolated stores, resolved per request from the X-Guardian-Token.

    Holds the profile's secret ``token``; this is an internal object and must never be
    serialized into a response.
    """

    name: str
    token: str
    whitelist: WhitelistStore
    request_store: RequestStore
    cache: VerdictCache
