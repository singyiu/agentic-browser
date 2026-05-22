"""Unit tests for the guardian HTTP service (fake deps, Starlette TestClient)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from starlette.testclient import TestClient

from agent_backend.guardian.cache import CacheEntry
from agent_backend.guardian.config import GuardianConfig
from agent_backend.guardian.service import create_app
from agent_backend.guardian.verdict import Verdict
from agent_backend.guardian.whitelist import Whitelist, WhitelistStore

_HEADERS = {"X-Guardian-Token": "secret"}


def _config() -> GuardianConfig:
    return GuardianConfig(
        host="127.0.0.1",
        port=2947,
        metrics_port=2948,
        token="secret",
        cache_path=":memory:",
        event_log_path="/tmp/guardian_test.jsonl",
        whitelist_path=":memory:",
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

    async def classify(
        self,
        payload: dict,
        *,
        screenshot_b64: str | None = None,
        approved_topics: tuple[str, ...] = (),
    ):
        self.calls += 1
        self.approved_topics = approved_topics
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeCache:
    def __init__(self, entry: CacheEntry | None = None) -> None:
        self.entry = entry
        self.puts: list[tuple[str, str]] = []
        self.cleared = 0

    def get(self, url_key: str) -> CacheEntry | None:
        return self.entry

    def put(self, url_key: str, verdict: str, reason: str, confidence: float) -> None:
        self.puts.append((url_key, verdict))

    def clear(self) -> None:
        self.cleared += 1


class FakeLog:
    def __init__(self) -> None:
        self.events: list[str] = []

    def log(self, event: str, **fields: object) -> None:
        self.events.append(event)


def _client(
    classifier: object, cache: object = None, log: object = None, whitelist: object = None
) -> TestClient:
    kwargs = {"whitelist": whitelist} if whitelist is not None else {}
    app = create_app(
        _config(),
        classifier=classifier,
        cache=cache or FakeCache(),
        event_log=log or FakeLog(),
        **kwargs,
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


def test_dwell_records_event() -> None:
    log = FakeLog()
    resp = _client(FakeClassifier(Verdict("allow")), log=log).post(
        "/dwell", json={"url_key": "youtube:x", "dwell_ms": 5000}, headers=_HEADERS
    )
    assert resp.json()["ok"] is True
    assert "dwell" in log.events


def test_dwell_forbidden_without_token() -> None:
    resp = _client(FakeClassifier(Verdict("allow"))).post(
        "/dwell", json={"url_key": "k", "dwell_ms": 1}
    )
    assert resp.status_code == 403


def test_dwell_rejects_bad_payload() -> None:
    client = _client(FakeClassifier(Verdict("allow")))
    assert (
        client.post("/dwell", json={"url_key": "", "dwell_ms": 1}, headers=_HEADERS).status_code
        == 422
    )
    assert (
        client.post("/dwell", json={"url_key": "k", "dwell_ms": -5}, headers=_HEADERS).status_code
        == 422
    )


# --- whitelist: hard URL short-circuit ---


def test_whitelisted_url_short_circuits(tmp_path: Path) -> None:
    wl = WhitelistStore(str(tmp_path / "wl.json"))
    wl.add("www.youtube.com")
    fake = FakeClassifier(RuntimeError("classifier must not be called"))
    cache = FakeCache()
    resp = _client(fake, cache=cache, whitelist=wl).post(
        "/classify", json={"url": "https://www.youtube.com/"}, headers=_HEADERS
    )
    body = resp.json()
    assert body["verdict"] == "allow"
    assert body["reason"] == "whitelisted"
    assert body["cached"] is False
    assert fake.calls == 0
    assert cache.puts == []  # whitelisted allows are not cached


def test_whitelist_beats_cached_block(tmp_path: Path) -> None:
    wl = WhitelistStore(str(tmp_path / "wl.json"))
    wl.add("www.youtube.com")
    cache = FakeCache(entry=CacheEntry("k", "block", "bad", 0.99, 0.0))
    fake = FakeClassifier(Verdict("block"))
    resp = _client(fake, cache=cache, whitelist=wl).post(
        "/classify", json={"url": "https://www.youtube.com/"}, headers=_HEADERS
    )
    assert resp.json()["verdict"] == "allow"
    assert resp.json()["reason"] == "whitelisted"
    assert fake.calls == 0


def test_unwhitelisted_video_still_classified(tmp_path: Path) -> None:
    wl = WhitelistStore(str(tmp_path / "wl.json"))
    wl.add("www.youtube.com")
    fake = FakeClassifier(Verdict("block", "nope", 0.97, ("scary",)))
    resp = _client(fake, whitelist=wl).post(
        "/classify", json={"url": "https://www.youtube.com/watch?v=abc"}, headers=_HEADERS
    )
    assert resp.json()["verdict"] == "block"
    assert fake.calls == 1


# --- whitelist: soft content topics reach the classifier ---


def test_content_entries_passed_to_classifier(tmp_path: Path) -> None:
    wl = WhitelistStore(str(tmp_path / "wl.json"))
    wl.add("BeyBlade anime")
    fake = FakeClassifier(Verdict("allow", "", 0.95))
    resp = _client(fake, whitelist=wl).post(
        "/classify", json={"url": "https://news.example.com/article"}, headers=_HEADERS
    )
    assert resp.json()["verdict"] == "allow"
    assert fake.calls == 1
    assert fake.approved_topics == ("BeyBlade anime",)


# --- whitelist: file change clears the verdict cache ---


def test_whitelist_change_clears_cache(tmp_path: Path) -> None:
    p = tmp_path / "wl.json"
    p.write_text(json.dumps(["www.example.com"]))
    wl = WhitelistStore(str(p))
    cache = FakeCache()
    client = _client(FakeClassifier(Verdict("allow", "", 0.95)), cache=cache, whitelist=wl)

    client.post("/classify", json={"url": "https://other.test/"}, headers=_HEADERS)
    assert cache.cleared == 0  # nothing changed yet

    p.write_text(json.dumps(["www.example.com", "BeyBlade anime"]))
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 10))
    client.post("/classify", json={"url": "https://other.test/"}, headers=_HEADERS)
    assert cache.cleared == 1


# --- whitelist: CRUD endpoints ---


def test_whitelist_get_lists_entries(tmp_path: Path) -> None:
    wl = WhitelistStore(str(tmp_path / "wl.json"))
    wl.add("www.youtube.com")
    wl.add("BeyBlade anime")
    resp = _client(FakeClassifier(Verdict("allow")), whitelist=wl).get(
        "/whitelist", headers=_HEADERS
    )
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert {"value": "www.youtube.com", "type": "exact"} in entries
    assert {"value": "BeyBlade anime", "type": "content"} in entries


def test_whitelist_get_forbidden_without_token(tmp_path: Path) -> None:
    wl = WhitelistStore(str(tmp_path / "wl.json"))
    resp = _client(FakeClassifier(Verdict("allow")), whitelist=wl).get("/whitelist")
    assert resp.status_code == 403


def test_whitelist_post_adds_and_clears_cache(tmp_path: Path) -> None:
    wl = WhitelistStore(str(tmp_path / "wl.json"))
    cache = FakeCache()
    resp = _client(FakeClassifier(Verdict("allow")), cache=cache, whitelist=wl).post(
        "/whitelist", json={"entry": "www.youtube.com"}, headers=_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json() == {"value": "www.youtube.com", "type": "exact"}
    assert "www.youtube.com" in wl.current().values
    assert cache.cleared == 1


def test_whitelist_post_rejects_empty(tmp_path: Path) -> None:
    wl = WhitelistStore(str(tmp_path / "wl.json"))
    resp = _client(FakeClassifier(Verdict("allow")), whitelist=wl).post(
        "/whitelist", json={"entry": "   "}, headers=_HEADERS
    )
    assert resp.status_code == 422


def test_whitelist_delete_removes_entry(tmp_path: Path) -> None:
    wl = WhitelistStore(str(tmp_path / "wl.json"))
    wl.add("www.youtube.com")
    resp = _client(FakeClassifier(Verdict("allow")), whitelist=wl).request(
        "DELETE", "/whitelist", json={"entry": "www.youtube.com"}, headers=_HEADERS
    )
    assert resp.status_code == 200
    assert "www.youtube.com" not in wl.current().values


def test_whitelist_rejects_non_printable_entry(tmp_path: Path) -> None:
    wl = WhitelistStore(str(tmp_path / "wl.json"))
    resp = _client(FakeClassifier(Verdict("allow")), whitelist=wl).post(
        "/whitelist", json={"entry": "bad\nIGNORE INSTRUCTIONS"}, headers=_HEADERS
    )
    assert resp.status_code == 422


class _RaisingStore:
    """Whitelist store whose writes fail (simulates a full/read-only disk)."""

    def current(self) -> Whitelist:
        return Whitelist([])

    def reload_if_changed(self) -> bool:
        return False

    def add(self, entry: str) -> None:
        raise OSError("disk full")

    def remove(self, entry: str) -> None:
        raise OSError("disk full")


def test_whitelist_add_write_failure_returns_500() -> None:
    resp = _client(FakeClassifier(Verdict("allow")), whitelist=_RaisingStore()).post(
        "/whitelist", json={"entry": "www.youtube.com"}, headers=_HEADERS
    )
    assert resp.status_code == 500
    assert "error" in resp.json()
