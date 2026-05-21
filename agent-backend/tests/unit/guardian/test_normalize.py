"""Unit tests for URL normalization."""

from __future__ import annotations

from agent_backend.guardian.normalize import normalize_url


def test_youtube_watch() -> None:
    assert normalize_url("https://www.youtube.com/watch?v=abc123&t=10") == "youtube:abc123"


def test_youtu_be() -> None:
    assert normalize_url("https://youtu.be/abc123?si=xyz") == "youtube:abc123"


def test_youtube_shorts() -> None:
    assert normalize_url("https://www.youtube.com/shorts/abc123") == "youtube:abc123"


def test_strips_utm_and_fragment() -> None:
    assert normalize_url("https://Example.com/Page?utm_source=x&q=cats#frag") == (
        "https://example.com/Page?q=cats"
    )


def test_lowercases_scheme_and_host() -> None:
    assert normalize_url("HTTPS://EXAMPLE.COM/a") == "https://example.com/a"


def test_preserves_meaningful_query() -> None:
    assert "q=cats" in normalize_url("https://example.com/s?q=cats")
