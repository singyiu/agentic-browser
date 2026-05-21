"""URL normalization for cache keying (mirrors extension/normalize.js)."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PREFIX = "utm_"
_TRACKING_KEYS = {"fbclid", "gclid", "ref", "ref_src", "source", "spm", "si"}
_YT_ID = re.compile(r"[A-Za-z0-9_-]{6,}")


def _youtube_id(host: str, path: str, query: dict[str, str]) -> str | None:
    h = host.removeprefix("www.")
    if h in ("youtube.com", "m.youtube.com", "music.youtube.com"):
        if path == "/watch" and query.get("v"):
            return query["v"]
        if path.startswith("/shorts/"):
            return path.split("/shorts/", 1)[1].split("/")[0]
        if path.startswith("/embed/"):
            return path.split("/embed/", 1)[1].split("/")[0]
    if h == "youtu.be":
        return path.lstrip("/").split("/")[0] or None
    return None


def normalize_url(url: str) -> str:
    """Return a stable cache key: YouTube videos collapse to ``youtube:<id>``;
    other URLs drop the fragment and tracking params and lowercase scheme/host."""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    path = parts.path or "/"
    query = dict(parse_qsl(parts.query))

    video_id = _youtube_id(host, path, query)
    if video_id and _YT_ID.fullmatch(video_id):
        return f"youtube:{video_id}"

    cleaned = {
        k: v
        for k, v in query.items()
        if not k.lower().startswith(_TRACKING_PREFIX) and k.lower() not in _TRACKING_KEYS
    }
    new_query = urlencode(sorted(cleaned.items()))
    return urlunsplit((parts.scheme.lower(), host, path, new_query, ""))


def extract_host(url_key: str) -> str:
    """Low-cardinality host label for metrics, derived from a normalized ``url_key``.

    ``youtube:<id>`` collapses to ``youtube.com``; otherwise the registered domain
    (naive eTLD+1) of the URL. Returns ``"unknown"`` when it cannot be derived."""
    key = (url_key or "").strip()
    if not key:
        return "unknown"
    if key.startswith("youtube:"):
        return "youtube.com"
    try:
        parts = urlsplit(key)
        host = (parts.netloc or parts.path).lower()
    except ValueError:
        return "unknown"
    host = host.split("/")[0].split(":")[0].removeprefix("www.")
    if not host:
        return "unknown"
    if "." not in host:
        return host[:64]
    return ".".join(host.split(".")[-2:])[:64]
