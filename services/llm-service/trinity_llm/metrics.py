"""
Prometheus metrics for the Trinity LLM Service.

Exposes counters and a histogram for request observability, plus a
``/metrics`` endpoint function that can be mounted on the FastAPI app.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)
from starlette.requests import Request
from starlette.responses import Response

# ---- Counters ----

llm_requests_total = Counter(
    "llm_requests_total",
    "Total number of LLM chat requests processed.",
    labelnames=("provider",),
)

llm_investigations_total = Counter(
    "llm_investigations_total",
    "Total number of transaction investigations performed.",
)

llm_sars_generated_total = Counter(
    "llm_sars_generated_total",
    "Total number of SAR narratives generated.",
)

# ---- Histograms ----

llm_request_duration_seconds = Histogram(
    "llm_request_duration_seconds",
    "Latency of LLM requests in seconds.",
    labelnames=("provider",),
    buckets=(
        0.05,
        0.1,
        0.25,
        0.5,
        1.0,
        2.5,
        5.0,
        10.0,
        30.0,
        60.0,
    ),
)


def metrics_endpoint(_request: Request) -> Response:
    """FastAPI endpoint that returns Prometheus-format metrics."""

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
