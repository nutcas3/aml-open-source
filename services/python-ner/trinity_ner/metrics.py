"""
Trinity Guard NER Service — Prometheus Metrics.

Defines the metrics registry and a helper to expose the /metrics endpoint
from a FastAPI application.
"""

from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

ner_requests_total = Counter(
    "ner_requests_total",
    "Total number of NER requests processed",
    ["endpoint"],
)

ner_entities_detected_total = Counter(
    "ner_entities_detected_total",
    "Total number of entities detected by the NER service",
)

ner_suspicious_found_total = Counter(
    "ner_suspicious_found_total",
    "Total number of suspicious entities found (sanctions matches)",
)

ner_inference_duration_seconds = Histogram(
    "ner_inference_duration_seconds",
    "Time spent running GLINER inference in seconds",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


def metrics_response() -> tuple[bytes, str]:
    """Return (body, content_type) for the /metrics endpoint."""
    return generate_latest(), CONTENT_TYPE_LATEST
