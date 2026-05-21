"""Unit tests for the guardian HTTP service (fake deps, Starlette TestClient)."""

from __future__ import annotations

from starlette.testclient import TestClient

from agent_backend.guardian.cache import CacheEntry
from agent_backend.guardian.config import GuardianConfig
from agent_backend.guardian.service import create_app
from agent_backend.guardian.verdict import Verdict

_HEADERS = {"X-Guardian-Token": "secret"}


def _config() -> GuardianConfig:
    return GuardianConfig(
        host="127.0.0.1",
        port=2947,
        metrics_port=2948,
        token="secret",
        cache_path=":memory:",
        event_log_path="/tmp/guardian_test.jsonl",
        classify_timeout_s=5.0,
        screenshot_confidence_threshold=0.6,
        enable_vision=False,
        model="m",
        config_dir="/tmp",
        oauth_token="t",
    )


class FakeClassifier:
    def __init__(self, result: object) -> None:
        self._result = result
        self.calls = 0

    async def classify(self, payload: dict, *, screenshot_b64: str | None = None):
        self.calls += 1
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeCache:
    def __init__(self, entry: CacheEntry | None = None) -> None:
        self.entry = entry
        self.puts: list[tuple[str, str]] = []

    def get(self, url_key: str) -> CacheEntry | None:
        return self.entry

    def put(self, url_key: str, verdict: str, reason: str, confidence: float) -> None:
        self.puts.append((url_key, verdict))


class FakeLog:
    def __init__(self) -> None:
        self.events: list[str] = []

    def log(self, event: str, **fields: object) -> None:
        self.events.append(event)


def _client(classifier: object, cache: object = None, log: object = None) -> TestClient:
    app = create_app(
        _config(), classifier=classifier, cache=cache or FakeCache(), event_log=log or FakeLog()
    )
    return TestClient(app)


def test_health() -> None:
    assert _client(FakeClassifier(Verdict("allow"))).get("/health").json()["status"] == "ok"


def test_forbidden_without_token() -> None:
    resp = _client(FakeClassifier(Verdict("allow"))).post("/classify", json={"url": "http://x"})
    assert resp.status_code == 403


def test_allow_caches_and_returns() -> None:
    cache = FakeCache()
    resp = _client(FakeClassifier(Verdict("allow", "", 0.95)), cache=cache).post(
        "/classify", json={"url": "http://x"}, headers=_HEADERS
    )
    assert resp.json()["verdict"] == "allow"
    assert cache.puts and cache.puts[0][1] == "allow"


def test_block() -> None:
    cache = FakeCache()
    resp = _client(FakeClassifier(Verdict("block", "nope", 0.97, ("violence",))), cache=cache).post(
        "/classify", json={"url": "http://x"}, headers=_HEADERS
    )
    assert resp.json()["verdict"] == "block"
    assert cache.puts[0][1] == "block"


def test_cache_hit_skips_classifier() -> None:
    fake = FakeClassifier(Verdict("block"))
    cache = FakeCache(entry=CacheEntry("k", "allow", "ok", 0.9, 0.0))
    resp = _client(fake, cache=cache).post(
        "/classify", json={"url": "http://x", "url_key": "k"}, headers=_HEADERS
    )
    assert resp.json()["verdict"] == "allow"
    assert resp.json()["cached"] is True
    assert fake.calls == 0


def test_fail_open_on_classifier_error() -> None:
    log = FakeLog()
    resp = _client(FakeClassifier(RuntimeError("boom")), log=log).post(
        "/classify", json={"url": "http://x"}, headers=_HEADERS
    )
    assert resp.json()["verdict"] == "allow"
    assert "fail_open" in log.events


def test_low_confidence_escalates_to_screenshot() -> None:
    resp = _client(FakeClassifier(Verdict("allow", "unsure", 0.3))).post(
        "/classify", json={"url": "http://x", "can_escalate": True}, headers=_HEADERS
    )
    assert resp.json()["verdict"] == "need_screenshot"


def test_no_escalation_when_screenshot_present() -> None:
    resp = _client(FakeClassifier(Verdict("allow", "unsure", 0.3))).post(
        "/classify",
        json={"url": "http://x", "can_escalate": False, "screenshot_b64": "abc"},
        headers=_HEADERS,
    )
    assert resp.json()["verdict"] == "allow"
