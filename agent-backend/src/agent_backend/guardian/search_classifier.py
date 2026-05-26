"""Search-query classification: parent keyword lists first, then age-aware AI safety.

Decision order, mirroring the page classifier's "hard rules then LLM":
  1. teen or Global allow-list match  -> allow (parent said yes)
  2. teen or Global block-list match  -> block (parent said no)
  3. otherwise, an age-aware AI safety judgment of the query

Steps 1-2 are synchronous whole-word lookups, so the parent's explicit lists are always
honored even if the AI step is slow or unavailable. The AI step (and its fail-open default)
lives in :meth:`Classifier.classify_search_query`, which never relaxes the ALWAYS-BLOCK floor.
"""

from __future__ import annotations

from .classifier import Classifier
from .config import DEFAULT_AGE
from .keyword_store import KeywordList
from .verdict import Verdict, allow


async def classify_search_query(
    query: str,
    *,
    teen_allow: KeywordList,
    global_allow: KeywordList,
    teen_block: KeywordList,
    global_block: KeywordList,
    classifier: Classifier,
    age: int = DEFAULT_AGE,
    policy: str = "",
) -> Verdict:
    """Return an allow/block :class:`Verdict` for ``query``; parent lists win before any AI call."""
    if teen_allow.matches(query) or global_allow.matches(query):
        return allow("search_allowed", 1.0)
    if teen_block.matches(query) or global_block.matches(query):
        return Verdict("block", "search_blocked", 1.0, ("parent_blocklist",))
    return await classifier.classify_search_query(query, age=age, policy=policy)
