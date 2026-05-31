"""Prometheus metrics for the guardian service.

The registry is injectable so tests (and repeated ``create_app`` calls) each get an
isolated registry instead of colliding on the process-global one. Counter names are
declared WITHOUT the ``_total`` suffix; prometheus_client appends it on exposition.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server

# Allowlist of category labels (the rubric.py taxonomy) plus safe buckets. Any value the
# classifier emits outside this set is recorded as "unknown" to bound label cardinality.
CATEGORY_LABELS: frozenset[str] = frozenset(
    {
        "adult_content",
        "graphic_violence",
        "self_harm",
        "hate",
        "illegal_dangerous",
        "gambling",
        "alcohol_tobacco_vaping",
        "harassment",
        "mature_themes",
        "scary",
        "dating",
        "none",
        "unknown",
    }
)

# Latency buckets (ms) spanning fast paths through the 180 s classify timeout.
_DURATION_BUCKETS = (50, 100, 250, 500, 1000, 2500, 5000, 15000, 30000, 60000, 120000, 180000)


class GuardianMetrics:
    """Owns a Prometheus registry and the guardian's metric instruments."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry if registry is not None else CollectorRegistry()
        self.classifications = Counter(
            "guardian_classifications",
            "Completed classifications by verdict (allow|block|fail_open).",
            ["verdict"],
            registry=self.registry,
        )
        self.category_hits = Counter(
            "guardian_category_hits",
            "Times a content category was matched during classification.",
            ["category"],
            registry=self.registry,
        )
        self.classification_duration_ms = Histogram(
            "guardian_classification_duration_ms",
            "End-to-end classification latency in milliseconds.",
            buckets=_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.visits = Counter(
            "guardian_visits",
            "Page visits by host (eTLD+1).",
            ["host"],
            registry=self.registry,
        )
        self.dwell_seconds = Counter(
            "guardian_dwell_seconds",
            "Accumulated dwell time in seconds by host and profile.",
            ["host", "profile"],
            registry=self.registry,
        )
        self.cache_hits = Counter(
            "guardian_cache_hits",
            "Classifications served from cache.",
            registry=self.registry,
        )
        self.whitelist_hits = Counter(
            "guardian_whitelist_hits",
            "Page visits allowed by a whitelist URL rule (classifier skipped), by host.",
            ["host"],
            registry=self.registry,
        )
        self.access_requests = Counter(
            "guardian_access_requests",
            "Access requests submitted by the teen from a block page, by host.",
            ["host"],
            registry=self.registry,
        )
        self.access_decisions = Counter(
            "guardian_access_decisions",
            "Parent decisions on access requests, by decision (approve|reject).",
            ["decision"],
            registry=self.registry,
        )
        self.prize_points_changes = Counter(
            "guardian_prize_points_changes",
            "Prize-point change events by profile and direction (grant|redeem).",
            ["profile", "direction"],
            registry=self.registry,
        )
        self.prize_points_balance = Gauge(
            "guardian_prize_points_balance",
            "Current prize-point balance per profile.",
            ["profile"],
            registry=self.registry,
        )

    def record_classification(
        self, verdict: str, categories: tuple[str, ...], duration_ms: float, host: str
    ) -> None:
        """Record a fresh (non-cached) allow/block classification."""
        self.classifications.labels(verdict=verdict).inc()
        self.classification_duration_ms.observe(duration_ms)
        self.visits.labels(host=host).inc()
        for category in categories:
            safe = category if category in CATEGORY_LABELS else "unknown"
            self.category_hits.labels(category=safe).inc()

    def record_cache_hit(self, host: str) -> None:
        """Record a verdict served from cache (still counts as a page visit)."""
        self.cache_hits.inc()
        self.visits.labels(host=host).inc()

    def record_fail_open(self, host: str) -> None:
        """Record a fail-open (classify error/timeout); the page was still visited."""
        self.classifications.labels(verdict="fail_open").inc()
        self.visits.labels(host=host).inc()

    def record_whitelist_hit(self, host: str) -> None:
        """Record a page allowed by a whitelist URL rule (still counts as a visit)."""
        self.whitelist_hits.labels(host=host).inc()
        self.visits.labels(host=host).inc()

    def record_dwell(self, host: str, profile: str, seconds: float) -> None:
        """Add observed time-on-page (seconds) for a host+profile. Negatives are ignored."""
        if seconds < 0:
            return
        self.dwell_seconds.labels(host=host, profile=profile).inc(seconds)

    def record_access_request(self, host: str) -> None:
        """Record a teen access request submitted from a block page."""
        self.access_requests.labels(host=host).inc()

    def record_access_decision(self, decision: str) -> None:
        """Record a parent decision (approve|reject) on an access request."""
        self.access_decisions.labels(decision=decision).inc()

    def record_prize_grant(self, profile: str, points: int, balance: int) -> None:
        """A parent granted ``points`` to ``profile``; ``balance`` is the new total."""
        if points > 0:
            self.prize_points_changes.labels(profile=profile, direction="grant").inc(points)
        self.prize_points_balance.labels(profile=profile).set(balance)

    def record_prize_redeem(self, profile: str, points: int, balance: int) -> None:
        """A teen redeemed ``points`` for bonus time; ``balance`` is the new total."""
        if points > 0:
            self.prize_points_changes.labels(profile=profile, direction="redeem").inc(points)
        self.prize_points_balance.labels(profile=profile).set(balance)

    def seed_prize_balance(self, profile: str, balance: int) -> None:
        """Initialize a profile's balance gauge at startup (no change event recorded).

        Keeps the 14-day balance line continuous across restarts: without this the gauge
        series would only appear after the first grant/redeem of the new process.
        """
        self.prize_points_balance.labels(profile=profile).set(balance)


def start_metrics_server(metrics: GuardianMetrics, port: int) -> None:  # pragma: no cover
    """Expose ``metrics`` via an HTTP server in Prometheus exposition format."""
    start_http_server(port, registry=metrics.registry)
