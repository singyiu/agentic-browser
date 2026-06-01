"""Unit tests for the guardian "Agent" feature: /version, /agent/chat, /agent/apply.

Reuses the HTTP harness (``_client``/``_config``/``FakeClassifier``/``FakeLog``/``_PIN``) defined
in ``test_service`` so these endpoint tests build the real Starlette app the same way.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from agent_backend import __version__ as BACKEND_VERSION
from agent_backend.guardian import service
from agent_backend.guardian.config import DEFAULT_AGENT_MODEL
from agent_backend.guardian.verdict import Verdict

from .test_service import _PIN, FakeClassifier, _client, _pm_client


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


# --- POST /agent/apply -----------------------------------------------------------------------


def _with_alice(tmp_path: Path, *, classifier: object | None = None) -> tuple[TestClient, object]:
    """A real-store client with one teen profile 'alice' created."""
    client, manager = _pm_client(tmp_path, classifier=classifier)
    assert client.post("/profiles", json={"name": "alice"}, headers=_PIN).status_code == 201
    return client, manager


def _apply(client: TestClient, action: str, profile: str, params: dict) -> object:
    return client.post(
        "/agent/apply",
        json={"action": action, "profile": profile, "params": params},
        headers=_PIN,
    )


def test_apply_whitelist_add(tmp_path: Path) -> None:
    client, manager = _with_alice(tmp_path)
    resp = _apply(client, "whitelist.add", "alice", {"entry": "khanacademy.org"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert "khanacademy.org" in manager.snapshot()["alice"].whitelist.current().values


def test_apply_blocklist_add_then_remove(tmp_path: Path) -> None:
    client, manager = _with_alice(tmp_path)
    _apply(client, "blocklist.add", "alice", {"entry": "tiktok.com"})
    assert "tiktok.com" in manager.snapshot()["alice"].blocklist.current().values
    _apply(client, "blocklist.remove", "alice", {"entry": "tiktok.com"})
    assert "tiktok.com" not in manager.snapshot()["alice"].blocklist.current().values


def test_apply_search_block_keyword_add(tmp_path: Path) -> None:
    client, manager = _with_alice(tmp_path)
    assert _apply(client, "search_block.add", "alice", {"entry": "violence"}).status_code == 200
    assert "violence" in manager.snapshot()["alice"].search_block.current().values


def test_apply_prize_grant(tmp_path: Path) -> None:
    client, manager = _with_alice(tmp_path)
    resp = _apply(client, "prize.grant", "alice", {"points": 50, "reason": "great week"})
    assert resp.status_code == 200
    assert resp.json()["result"]["balance"] == 50
    assert manager.snapshot()["alice"].prize_point_store.balance() == 50


def test_apply_prize_grant_invalid_points(tmp_path: Path) -> None:
    client, _ = _with_alice(tmp_path)
    assert _apply(client, "prize.grant", "alice", {"points": 0}).status_code == 422
    assert _apply(client, "prize.grant", "alice", {"points": 10_000_000}).status_code == 422


def test_apply_prize_grant_requires_named_child(tmp_path: Path) -> None:
    client, _ = _with_alice(tmp_path)
    # Global is never a prize target.
    assert _apply(client, "prize.grant", "global", {"points": 10}).status_code == 404


def test_apply_prompt_set(tmp_path: Path) -> None:
    client, manager = _with_alice(tmp_path)
    resp = _apply(client, "prompt.set", "alice", {"prompt": "Block gambling content."})
    assert resp.status_code == 200
    assert manager.snapshot()["alice"].prompt_store.current() == "Block gambling content."


def test_apply_time_policy_set(tmp_path: Path) -> None:
    # The dispatcher runs the NL→JSON conversion through the classifier; feed it valid policy JSON.
    fake = FakeClassifier(Verdict("allow"), rule_result='{"daily_minutes": {"default": 120}}')
    client, manager = _with_alice(tmp_path, classifier=fake)
    resp = _apply(client, "time_policy.set", "alice", {"text": "2 hours a day"})
    assert resp.status_code == 200
    assert manager.snapshot()["alice"].time_policy.current().daily_minutes.get("default") == 120


def test_apply_unknown_action_422(tmp_path: Path) -> None:
    client, _ = _with_alice(tmp_path)
    assert _apply(client, "delete_profile", "alice", {}).status_code == 422


def test_apply_destructive_action_422(tmp_path: Path) -> None:
    client, _ = _with_alice(tmp_path)
    # PIN change / profile deletion / token regen are deliberately NOT in the registry.
    assert _apply(client, "pin.change", "alice", {"pin": "9999"}).status_code == 422


def test_apply_unknown_profile_404(tmp_path: Path) -> None:
    client, _ = _with_alice(tmp_path)
    assert _apply(client, "whitelist.add", "nobody", {"entry": "x.com"}).status_code == 404


def test_apply_bad_entry_422(tmp_path: Path) -> None:
    client, _ = _with_alice(tmp_path)
    assert _apply(client, "whitelist.add", "alice", {"entry": ""}).status_code == 422
    assert _apply(client, "whitelist.add", "alice", {"entry": "a\nb"}).status_code == 422
    assert _apply(client, "whitelist.add", "alice", {"entry": "x" * 513}).status_code == 422


def test_apply_params_not_object_422(tmp_path: Path) -> None:
    client, _ = _with_alice(tmp_path)
    resp = client.post(
        "/agent/apply",
        json={"action": "whitelist.add", "profile": "alice", "params": "nope"},
        headers=_PIN,
    )
    assert resp.status_code == 422


def test_apply_requires_pin(tmp_path: Path) -> None:
    client, _ = _with_alice(tmp_path)
    resp = client.post(
        "/agent/apply",
        json={"action": "whitelist.add", "profile": "alice", "params": {"entry": "x.com"}},
    )
    assert resp.status_code == 403
