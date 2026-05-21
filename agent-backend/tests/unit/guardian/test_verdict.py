"""Unit tests for verdict parsing (fail-open)."""

from __future__ import annotations

from agent_backend.guardian.verdict import parse_verdict


def test_clean_json() -> None:
    v = parse_verdict('{"verdict":"block","reason":"r","confidence":0.9,"categories":["violence"]}')
    assert v.verdict == "block"
    assert v.confidence == 0.9
    assert v.categories == ("violence",)


def test_prose_wrapped_json() -> None:
    v = parse_verdict('Sure! {"verdict":"allow","confidence":0.8} hope that helps')
    assert v.verdict == "allow"


def test_invalid_json_fails_open() -> None:
    assert parse_verdict("not json at all").verdict == "allow"


def test_missing_verdict_fails_open() -> None:
    assert parse_verdict('{"reason":"x"}').verdict == "allow"


def test_unknown_verdict_fails_open() -> None:
    assert parse_verdict('{"verdict":"maybe"}').verdict == "allow"


def test_need_screenshot() -> None:
    assert (
        parse_verdict('{"verdict":"need_screenshot","confidence":0.4}').verdict == "need_screenshot"
    )


def test_confidence_is_clamped() -> None:
    assert parse_verdict('{"verdict":"allow","confidence":5}').confidence == 1.0
