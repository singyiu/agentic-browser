"""The per-teen runtime: a profile's live, isolated stores.

Extracted into its own module so both the HTTP service and the profile manager can
depend on it without importing each other (which would be a circular import).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import ConfigError
from .access_requests import RequestStore
from .blocklist import BlocklistStore
from .cache import VerdictCache
from .keyword_store import KeywordStore
from .profiles import Profile
from .prompt import PromptStore
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
    blocklist: BlocklistStore
    request_store: RequestStore
    cache: VerdictCache
    prompt_store: PromptStore
    search_allow: KeywordStore
    search_block: KeywordStore
    age: int


def build_runtime(profile: Profile) -> ProfileRuntime:
    """Open a profile's stores into a live runtime, creating its data dirs first.

    Raises :class:`ConfigError` if a data directory cannot be created.
    """
    for path in (
        profile.whitelist_path,
        profile.blocklist_path,
        profile.requests_path,
        profile.cache_path,
        profile.prompt_path,
        profile.search_allow_path,
        profile.search_block_path,
    ):
        try:
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError(
                f"Cannot create data directory for profile {profile.name!r}: {exc}"
            ) from exc
    return ProfileRuntime(
        name=profile.name,
        token=profile.token,
        whitelist=WhitelistStore(profile.whitelist_path),
        blocklist=BlocklistStore(profile.blocklist_path),
        request_store=RequestStore(profile.requests_path),
        cache=VerdictCache(profile.cache_path),
        prompt_store=PromptStore(profile.prompt_path),
        search_allow=KeywordStore(profile.search_allow_path),
        search_block=KeywordStore(profile.search_block_path),
        age=profile.age,
    )
