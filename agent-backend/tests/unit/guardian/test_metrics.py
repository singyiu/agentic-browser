"""Unit tests for GuardianMetrics (injected registry, no global state)."""

from __future__ import annotations

from prometheus_client import CollectorRegistry

from agent_backend.guardian.metrics import CATEGORY_LABELS, GuardianMetrics


def _metrics() -> GuardianMetrics:
    return GuardianMetrics(registry=CollectorRegistry())


def _val(m: GuardianMetrics, name: str, labels: dict[str, str] | None = None) -> float | None:
    return m.registry.get_sample_value(name, labels or {})


def test_record_classification_counts_verdict_visit_and_category() -> None:
    m = _metrics()
    m.record_classification("block", ("scary",), 1200, "youtube.com")
    assert _val(m, "guardian_classifications_total", {"verdict": "block"}) == 1.0
    assert _val(m, "guardian_visits_total", {"host": "youtube.com"}) == 1.0
    assert _val(m, "guardian_category_hits_total", {"category": "scary"}) == 1.0


def test_unknown_category_maps_to_unknown_bucket() -> None:
    m = _metrics()
    m.record_classification("allow", ("totally_made_up",), 10, "example.com")
    assert _val(m, "guardian_category_hits_total", {"category": "unknown"}) == 1.0
    assert _val(m, "guardian_category_hits_total", {"category": "totally_made_up"}) is None


def test_record_classification_observes_duration() -> None:
    m = _metrics()
    m.record_classification("allow", (), 1500, "example.com")
    assert _val(m, "guardian_classification_duration_ms_count") == 1.0
    assert _val(m, "guardian_classification_duration_ms_sum") == 1500.0


def test_record_cache_hit_counts_hit_and_visit() -> None:
    m = _metrics()
    m.record_cache_hit("example.com")
    assert _val(m, "guardian_cache_hits_total") == 1.0
    assert _val(m, "guardian_visits_total", {"host": "example.com"}) == 1.0


def test_record_fail_open_counts_verdict_and_visit() -> None:
    m = _metrics()
    m.record_fail_open("example.com")
    assert _val(m, "guardian_classifications_total", {"verdict": "fail_open"}) == 1.0
    assert _val(m, "guardian_visits_total", {"host": "example.com"}) == 1.0


def test_record_dwell_accumulates_seconds() -> None:
    m = _metrics()
    m.record_dwell("youtube.com", 12.5)
    m.record_dwell("youtube.com", 7.5)
    assert _val(m, "guardian_dwell_seconds_total", {"host": "youtube.com"}) == 20.0


def test_record_dwell_ignores_negative() -> None:
    m = _metrics()
    m.record_dwell("example.com", -5.0)
    assert _val(m, "guardian_dwell_seconds_total", {"host": "example.com"}) is None


def test_record_whitelist_hit_counts_hit_and_visit() -> None:
    m = _metrics()
    m.record_whitelist_hit("youtube.com")
    assert _val(m, "guardian_whitelist_hits_total", {"host": "youtube.com"}) == 1.0
    assert _val(m, "guardian_visits_total", {"host": "youtube.com"}) == 1.0


def test_record_access_request_counts_by_host() -> None:
    m = _metrics()
    m.record_access_request("example.com")
    m.record_access_request("example.com")
    assert _val(m, "guardian_access_requests_total", {"host": "example.com"}) == 2.0


def test_record_access_decision_counts_by_decision() -> None:
    m = _metrics()
    m.record_access_decision("approve")
    m.record_access_decision("reject")
    assert _val(m, "guardian_access_decisions_total", {"decision": "approve"}) == 1.0
    assert _val(m, "guardian_access_decisions_total", {"decision": "reject"}) == 1.0


def test_registries_are_isolated() -> None:
    m1, m2 = _metrics(), _metrics()
    m1.record_cache_hit("a.com")
    assert _val(m1, "guardian_cache_hits_total") == 1.0
    assert _val(m2, "guardian_cache_hits_total") in (None, 0.0)


def test_category_allowlist_is_exported() -> None:
    assert "scary" in CATEGORY_LABELS
    assert "unknown" in CATEGORY_LABELS
    assert "none" in CATEGORY_LABELS
