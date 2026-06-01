"""Unit tests for the guardian "Agent" feature: /version, /agent/chat, /agent/apply.

Reuses the HTTP harness (``_client``/``_config``/``FakeClassifier``/``FakeLog``/``_PIN``) defined
in ``test_service`` so these endpoint tests build the real Starlette app the same way.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_backend import __version__ as BACKEND_VERSION
from agent_backend.guardian import service
from agent_backend.guardian.config import DEFAULT_AGENT_MODEL
from agent_backend.guardian.verdict import Verdict

from .test_service import _PIN, FakeClassifier, _client


def _ok_classifier() -> FakeClassifier:
    return FakeClassifier(Verdict("allow"))


def _chat(envelope: object) -> FakeClassifier:
    """A classifier whose ``generate`` returns ``envelope`` (the model's raw chat output)."""
    return FakeClassifier(Verdict("allow"), rule_result=envelope)


def _ask(content: str = "hi") -> dict:
    return {"messages": [{"role": "user", "content": content}]}


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


# --- POST /agent/chat ------------------------------------------------------------------------


def test_chat_returns_envelope() -> None:
    env = json.dumps(
        {
            "reply": "Hello!",
            "proposals": [
                {
                    "action": "blocklist.add",
                    "profile": "default",
                    "params": {"entry": "tiktok.com"},
                    "rationale": "Most-blocked host.",
                }
            ],
            "suggestions": ["Show recent activity"],
        }
    )
    fake = _chat(env)
    resp = _client(fake).post("/agent/chat", json=_ask(), headers=_PIN)
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "Hello!"
    assert data["proposals"][0]["action"] == "blocklist.add"
    assert data["proposals"][0]["params"] == {"entry": "tiktok.com"}
    assert data["suggestions"] == ["Show recent activity"]
    # The flagship chat runs on the stronger agent model, not the classifier model.
    assert fake.generate_model == DEFAULT_AGENT_MODEL


def test_chat_malformed_output_falls_back_to_reply() -> None:
    resp = _client(_chat("I'm not JSON, sorry.")).post("/agent/chat", json=_ask(), headers=_PIN)
    data = resp.json()
    assert data["reply"] == "I'm not JSON, sorry."
    assert data["proposals"] == []
    assert data["suggestions"] == []


def test_chat_partial_envelope_missing_keys() -> None:
    resp = _client(_chat('{"reply":"just text"}')).post("/agent/chat", json=_ask(), headers=_PIN)
    data = resp.json()
    assert data["reply"] == "just text"
    assert data["proposals"] == []
    assert data["suggestions"] == []


def test_chat_unknown_action_stripped() -> None:
    env = json.dumps(
        {
            "reply": "ok",
            "proposals": [
                {"action": "delete_profile", "profile": "default", "params": {}, "rationale": "x"},
                {
                    "action": "whitelist.add",
                    "profile": "default",
                    "params": {"entry": "khanacademy.org"},
                    "rationale": "y",
                },
            ],
            "suggestions": [],
        }
    )
    data = _client(_chat(env)).post("/agent/chat", json=_ask(), headers=_PIN).json()
    assert [p["action"] for p in data["proposals"]] == ["whitelist.add"]


def test_chat_history_bounded_to_20() -> None:
    fake = _chat('{"reply":"ok"}')
    msgs = {"messages": [{"role": "user", "content": f"m{i}"} for i in range(25)]}
    _client(fake).post("/agent/chat", json=msgs, headers=_PIN)
    convo = fake.generate_user_prompt
    assert "m24" in convo  # newest kept
    assert "m0" not in convo  # oldest dropped (only the last 20 are sent)
    assert "m4" not in convo


def test_chat_must_end_with_user_message() -> None:
    msgs = {
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
    }
    resp = _client(_ok_classifier()).post("/agent/chat", json=msgs, headers=_PIN)
    assert resp.status_code == 422


def test_chat_empty_messages_422() -> None:
    resp = _client(_ok_classifier()).post("/agent/chat", json={"messages": []}, headers=_PIN)
    assert resp.status_code == 422


def test_chat_invalid_json_body_422() -> None:
    resp = _client(_ok_classifier()).post(
        "/agent/chat",
        content="not json",
        headers={**_PIN, "Content-Type": "application/json"},
    )
    assert resp.status_code == 422


def test_chat_unknown_profile_404() -> None:
    body = {**_ask(), "profile": "nobody"}
    resp = _client(_ok_classifier()).post("/agent/chat", json=body, headers=_PIN)
    assert resp.status_code == 404


def test_chat_known_profile_scopes_context() -> None:
    fake = _chat('{"reply":"ok"}')
    body = {**_ask(), "profile": "default"}
    resp = _client(fake).post("/agent/chat", json=body, headers=_PIN)
    assert resp.status_code == 200
    assert "default" in fake.generate_system_prompt


def test_chat_requires_pin() -> None:
    resp = _client(_ok_classifier()).post("/agent/chat", json=_ask())
    assert resp.status_code == 403


def test_chat_503_when_pin_unset() -> None:
    resp = _client(_ok_classifier(), parent_pin="").post("/agent/chat", json=_ask(), headers=_PIN)
    assert resp.status_code == 503


def test_chat_llm_error_502() -> None:
    fake = FakeClassifier(Verdict("allow"), rule_result=RuntimeError("boom"))
    resp = _client(fake).post("/agent/chat", json=_ask(), headers=_PIN)
    assert resp.status_code == 502
