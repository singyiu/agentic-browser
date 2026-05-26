"""Unit tests for search_classifier.classify_search_query (parent lists first, then AI)."""

from __future__ import annotations

from agent_backend.guardian.keyword_store import KeywordList
from agent_backend.guardian.search_classifier import classify_search_query
from agent_backend.guardian.verdict import Verdict


class _FakeClassifier:
    """Duck-typed stand-in: records whether the AI step ran and what age/policy it saw."""

    def __init__(self, result: Verdict) -> None:
        self._result = result
        self.calls = 0
        self.last_age: int | None = None
        self.last_policy: str | None = None

    async def classify_search_query(
        self, query: str, *, age: int = 10, policy: str = ""
    ) -> Verdict:
        self.calls += 1
        self.last_age = age
        self.last_policy = policy
        return self._result


_EMPTY = KeywordList([])


async def _classify(
    query: str,
    *,
    teen_allow: KeywordList = _EMPTY,
    global_allow: KeywordList = _EMPTY,
    teen_block: KeywordList = _EMPTY,
    global_block: KeywordList = _EMPTY,
    classifier: _FakeClassifier | None = None,
    age: int = 10,
    policy: str = "",
) -> Verdict:
    fc = classifier or _FakeClassifier(Verdict("allow", "ai-allow"))
    return await classify_search_query(
        query,
        teen_allow=teen_allow,
        global_allow=global_allow,
        teen_block=teen_block,
        global_block=global_block,
        classifier=fc,
        age=age,
        policy=policy,
    )


async def test_teen_allow_list_wins_without_ai() -> None:
    fc = _FakeClassifier(Verdict("block", "ai-block"))
    verdict = await _classify(
        "minecraft tutorial", teen_allow=KeywordList(["minecraft"]), classifier=fc
    )
    assert verdict.verdict == "allow"
    assert fc.calls == 0  # parent allow-list short-circuits the AI


async def test_global_allow_list_wins() -> None:
    verdict = await _classify("khan academy", global_allow=KeywordList(["khan academy"]))
    assert verdict.verdict == "allow"


async def test_teen_block_list_blocks_without_ai() -> None:
    fc = _FakeClassifier(Verdict("allow", "ai-allow"))
    verdict = await _classify(
        "buy gambling chips", teen_block=KeywordList(["gambling"]), classifier=fc
    )
    assert verdict.verdict == "block"
    assert fc.calls == 0


async def test_global_block_list_blocks() -> None:
    verdict = await _classify("free porn", global_block=KeywordList(["porn"]))
    assert verdict.verdict == "block"


async def test_allow_wins_over_block_when_both_match() -> None:
    verdict = await _classify(
        "minecraft gambling",
        teen_allow=KeywordList(["minecraft"]),
        teen_block=KeywordList(["gambling"]),
    )
    assert verdict.verdict == "allow"


async def test_no_match_falls_through_to_ai_allow() -> None:
    fc = _FakeClassifier(Verdict("allow", "ai-allow"))
    verdict = await _classify("how do volcanoes work", classifier=fc)
    assert verdict.verdict == "allow"
    assert fc.calls == 1


async def test_no_match_falls_through_to_ai_block() -> None:
    fc = _FakeClassifier(Verdict("block", "ai-block"))
    verdict = await _classify("how to build a weapon", classifier=fc)
    assert verdict.verdict == "block"


async def test_ai_receives_age_and_policy() -> None:
    fc = _FakeClassifier(Verdict("allow"))
    await _classify("something neutral", classifier=fc, age=15, policy="be lenient")
    assert fc.last_age == 15
    assert fc.last_policy == "be lenient"
