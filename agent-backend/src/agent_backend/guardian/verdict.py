"""The classification verdict and a fail-open JSON parser."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

VALID_VERDICTS = {"allow", "block", "need_screenshot"}
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True, slots=True)
class Verdict:
    verdict: str  # "allow" | "block" | "need_screenshot"
    reason: str = ""
    confidence: float = 0.0
    categories: tuple[str, ...] = ()


def allow(reason: str = "", confidence: float = 0.0) -> Verdict:
    """The fail-open default."""
    return Verdict("allow", reason, confidence, ())


def parse_verdict(text: str) -> Verdict:
    """Extract the verdict JSON from model output. Fail-open (allow) on any problem
    so a malformed response never blocks a child's page."""
    match = _JSON_RE.search(text or "")
    if not match:
        return allow("parse_error: no JSON in response")
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        return allow("parse_error: invalid JSON")
    if not isinstance(data, dict):
        return allow("parse_error: not an object")

    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in VALID_VERDICTS:
        return allow("parse_error: unknown verdict")

    raw_categories = data.get("categories") or data.get("categories_matched") or []
    categories = tuple(str(c) for c in raw_categories) if isinstance(raw_categories, list) else ()
    try:
        confidence = float(data.get("confidence", 0.0))
    except (ValueError, TypeError):
        confidence = 0.0
    return Verdict(verdict, str(data.get("reason", "")), max(0.0, min(1.0, confidence)), categories)
