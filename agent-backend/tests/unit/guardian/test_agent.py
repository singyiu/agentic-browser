"""Unit tests for the guardian "Agent" feature: /version, /agent/chat, /agent/apply.

Reuses the HTTP harness (``_client``/``_config``/``FakeClassifier``/``FakeLog``/``_PIN``) defined
in ``test_service`` so these endpoint tests build the real Starlette app the same way.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_backend import __version__ as BACKEND_VERSION
from agent_backend.guardian import service
from agent_backend.guardian.config import DEFAULT_AGENT_MODEL
from agent_backend.guardian.verdict import Verdict

from .test_service import _PIN, FakeClassifier, _client


def _ok_classifier() -> FakeClassifier:
    return FakeClassifier(Verdict("allow"))


# --- GET /version ----------------------------------------------------------------------------


def test_version_returns_all_fields() -> None:
    resp = _client(_ok_classifier()).get("/version", headers=_PIN)
    assert resp.status_code == 200
    data = resp.json()
    assert data["guardian"] == BACKEND_VERSION
    assert data["extension"] == "0.2.6"
    assert data["grafana"] == {"lgtm": "0.11.6", "alloy": "v1.10.0"}
    assert data["model"] == DEFAULT_AGENT_MODEL


def test_version_missing_sources_graceful(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Point the repo root at an empty dir → manifest + compose unreadable → null, never 500.
    monkeypatch.setattr(service, "_REPO_ROOT", tmp_path)
    resp = _client(_ok_classifier()).get("/version", headers=_PIN)
    assert resp.status_code == 200
    data = resp.json()
    assert data["extension"] is None
    assert data["grafana"] == {"lgtm": None, "alloy": None}
    assert data["guardian"] == BACKEND_VERSION  # still present
    assert data["model"] == DEFAULT_AGENT_MODEL


def test_version_requires_pin() -> None:
    assert _client(_ok_classifier()).get("/version").status_code == 403


def test_version_503_when_pin_unset() -> None:
    resp = _client(_ok_classifier(), parent_pin="").get("/version", headers=_PIN)
    assert resp.status_code == 503
