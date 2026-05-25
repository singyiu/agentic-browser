"""Parental blocklist: explicit deny rules — the mirror of the whitelist.

Storage and matching are identical to the whitelist (exact/wildcard URL rules + content
topics, auto-detected by :func:`agent_backend.guardian.whitelist.classify_entry`); only
the *meaning* differs, and that lives in the ``classify()`` decision path:

- exact / wildcard rules **hard-block** the URL (the classifier is skipped);
- content rules are surfaced to the classifier as parent-*disallowed* topics.

A missing or malformed file yields an empty blocklist (fails safe: nothing is wrongly
blocked).
"""

from __future__ import annotations

from .whitelist import WhitelistStore


class BlocklistStore(WhitelistStore):
    """Owns a profile's blocklist file; identical storage/reload to ``WhitelistStore``.

    Subclassed only for a distinct, self-documenting type (``rt.blocklist: BlocklistStore``).
    The deny semantics are applied where the rules are consulted, not here.
    """
