"""
Tests for the Trinity Guard NER service.

Uses httpx.AsyncClient against the FastAPI ASGI app with a fake GLINER
model injected via dependency injection — no real model is loaded.
"""

from __future__ import annotations

from typing import Any, List

import pytest
from httpx import ASGITransport, AsyncClient

from trinity_ner import ner_service as ner_module
from trinity_ner.ner_service import MarbleNERService, app, get_ner_service


# ---------------------------------------------------------------------------
# Fake GLINER model (test double — dependency injection, not a mock fallback)
# ---------------------------------------------------------------------------


class FakeGLiNERModel:
    """A fake GLINER model that returns predetermined entities.

    It scans the input text for known spans and emits labelled entities,
    mimicking the shape of GLiNER.predict_entities output.
    """

    KNOWN_SPANS: list[tuple[str, str, str]] = [
        # (substring, label, entity_text)
        ("maurice nyanja", "Person", "Maurice Nyanja"),
        ("m. emmanuel", "Person", "M. Emmanuel"),
        ("john doe", "Person", "John Doe"),
        ("moneycorp", "Company", "Moneycorp"),
        ("money corp", "Company", "Money Corp"),
        ("acme", "Company", "Acme"),
        ("kenya", "Country", "Kenya"),
        ("canada", "Country", "Canada"),
        ("money laundering", "Illegal Activity", "money laundering"),
        ("structuring", "Illegal Activity", "structuring"),
    ]

    def predict_entities(
        self, text: str, labels: List[str], **kwargs: Any
    ) -> List[dict]:
        text_lower = text.lower()
        results: List[dict] = []
        for substring, label, entity_text in self.KNOWN_SPANS:
            if label not in labels:
                continue
            if substring in text_lower:
                results.append({"label": label, "text": entity_text})
        return results


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_service() -> MarbleNERService:
    """A MarbleNERService backed by the fake GLINER model (no Redis)."""
    return MarbleNERService(model=FakeGLiNERModel())


@pytest.fixture
async def client(fake_service: MarbleNERService):
    """An async HTTP client wired to the app with the fake service injected."""
    # Inject the fake service so the startup event skips real GLINER loading.
    ner_module.ner_service = fake_service
    # Also override the dependency for explicitness.
    app.dependency_overrides[get_ner_service] = lambda: fake_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    ner_module.ner_service = None


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_endpoint(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["model_loaded"] is True
    assert body["sanctions_db_size"] > 0
    assert body["version"] == "2.0.0"


# ---------------------------------------------------------------------------
# /detect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_non_suspicious(client: AsyncClient) -> None:
    resp = await client.post(
        "/detect",
        json={"text": "John Doe sent money to Acme in Canada."},
    )
    assert resp.status_code == 200
    entities = resp.json()["entities"]
    texts = {e["text"] for e in entities}
    assert "John Doe" in texts
    assert "Acme" in texts
    assert "Canada" in texts
    # None of these match the sanctions list.
    assert all(e["suspicious"] is False for e in entities)
    assert all(e["sanctions_matches"] == [] for e in entities)


@pytest.mark.asyncio
async def test_detect_suspicious_entity(client: AsyncClient) -> None:
    resp = await client.post(
        "/detect",
        json={"text": "M. Emmanuel transferred funds to Moneycorp."},
    )
    assert resp.status_code == 200
    entities = resp.json()["entities"]
    by_text = {e["text"]: e for e in entities}

    # "M. Emmanuel" is in the sanctions DB directly.
    emmanuel = by_text["M. Emmanuel"]
    assert emmanuel["suspicious"] is True
    assert len(emmanuel["sanctions_matches"]) >= 1
    match = emmanuel["sanctions_matches"][0]
    assert match["sanction_id"] == "sanction_001"
    assert match["match_type"] in ("direct", "alias")

    # "Moneycorp" is also sanctioned.
    moneycorp = by_text["Moneycorp"]
    assert moneycorp["suspicious"] is True
    assert moneycorp["sanctions_matches"][0]["sanction_id"] == "sanction_004"

    # Entity type must NOT be mutated.
    assert emmanuel["type"] == "Person"
    assert moneycorp["type"] == "Company"


@pytest.mark.asyncio
async def test_detect_with_custom_labels(client: AsyncClient) -> None:
    resp = await client.post(
        "/detect",
        json={
            "text": "Kenya is a country.",
            "labels": ["Country"],
        },
    )
    assert resp.status_code == 200
    entities = resp.json()["entities"]
    assert len(entities) == 1
    assert entities[0]["type"] == "Country"
    assert entities[0]["text"] == "Kenya"


# ---------------------------------------------------------------------------
# /analyze
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_non_suspicious(client: AsyncClient) -> None:
    resp = await client.post(
        "/analyze",
        json={
            "id": "tx-001",
            "description": "Salary payment",
            "sender": "John Doe",
            "receiver": "Acme",
            "amount": 1500.00,
            "currency": "USD",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["transaction_id"] == "tx-001"
    assert body["suspicious_count"] == 0
    assert body["is_suspicious"] is False
    assert body["risk_level"] == "LOW"
    assert isinstance(body["entities"], list)
    assert len(body["entities"]) >= 2


@pytest.mark.asyncio
async def test_analyze_suspicious(client: AsyncClient) -> None:
    resp = await client.post(
        "/analyze",
        json={
            "id": "tx-002",
            "description": "money laundering scheme",
            "sender": "M. Emmanuel",
            "receiver": "Moneycorp",
            "amount": 999999.00,
            "currency": "USD",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["transaction_id"] == "tx-002"
    # Both sender and receiver are sanctioned.
    assert body["suspicious_count"] >= 2
    assert body["is_suspicious"] is True
    assert body["risk_level"] == "HIGH"
    # Verify suspicious flag + matches on entities.
    suspicious = [e for e in body["entities"] if e["suspicious"]]
    assert len(suspicious) >= 2
    for e in suspicious:
        assert e["sanctions_matches"]
        # Type must remain unmutated.
        assert "(SUSPICIOUS)" not in e["type"]


@pytest.mark.asyncio
async def test_analyze_single_suspicious_is_medium(client: AsyncClient) -> None:
    resp = await client.post(
        "/analyze",
        json={
            "id": "tx-003",
            "description": "consulting fee",
            "sender": "John Doe",
            "receiver": "Moneycorp",
            "amount": 5000.00,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["suspicious_count"] == 1
    assert body["is_suspicious"] is True
    assert body["risk_level"] == "MEDIUM"


# ---------------------------------------------------------------------------
# /metrics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_endpoint(client: AsyncClient) -> None:
    # Generate some traffic first.
    await client.post("/detect", json={"text": "John Doe in Canada."})
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "ner_requests_total" in resp.text
    assert "ner_entities_detected_total" in resp.text


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_root_endpoint(client: AsyncClient) -> None:
    resp = await client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "Trinity Guard NER Service"
    assert body["status"] == "running"
