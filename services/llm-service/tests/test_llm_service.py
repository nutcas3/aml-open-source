"""
Tests for the Trinity LLM Service.

Uses a FakeProvider (dependency injection) — no real API calls are made.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from trinity_llm.llm_service import (
    ChatRequest,
    ChatResponse,
    InvestigationRequest,
    InvestigationResponse,
    TrinityLLMService,
    app,
)
from trinity_llm.providers import ChatMessage, LLMResponse


# ---------------------------------------------------------------------------
# Fake provider — a test double implementing the LLMProvider protocol
# ---------------------------------------------------------------------------


class FakeProvider:
    """In-memory LLM provider for tests.

    Implements the ``LLMProvider`` protocol without any network calls.
    """

    name = "fake"

    def __init__(
        self,
        chat_content: str = "Fake LLM response",
        health_ok: bool = True,
    ) -> None:
        self._chat_content = chat_content
        self._health_ok = health_ok
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self,
        messages: list[ChatMessage],
        json_mode: bool = False,
    ) -> LLMResponse:
        self.calls.append(
            {
                "messages": [m.to_dict() for m in messages],
                "json_mode": json_mode,
            }
        )
        return LLMResponse(
            content=self._chat_content,
            model="fake-model",
            provider=self.name,
        )

    async def health(self) -> bool:
        return self._health_ok


class FailingProvider:
    """Provider that always raises — for testing 503 handling."""

    name = "failing"

    async def chat(
        self,
        messages: list[ChatMessage],
        json_mode: bool = False,
    ) -> LLMResponse:
        raise ConnectionError("provider backend is down")

    async def health(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def fake_service(fake_provider: FakeProvider) -> TrinityLLMService:
    return TrinityLLMService(provider=fake_provider)


@pytest.fixture
def failing_service() -> TrinityLLMService:
    return TrinityLLMService(provider=FailingProvider())


@pytest.fixture
async def client(fake_service: TrinityLLMService):
    """ASGI test client wired to a service backed by FakeProvider."""

    # Swap the module-level service so route handlers use the fake.
    import trinity_llm.llm_service as mod

    original = mod.llm_service
    mod.llm_service = fake_service
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        mod.llm_service = original


@pytest.fixture
async def failing_client(failing_service: TrinityLLMService):
    """ASGI test client wired to a service whose provider always fails."""

    import trinity_llm.llm_service as mod

    original = mod.llm_service
    mod.llm_service = failing_service
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        mod.llm_service = original


# ---------------------------------------------------------------------------
# /chat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_returns_response(client: AsyncClient, fake_provider: FakeProvider):
    resp = await client.post(
        "/chat",
        json={"text": "Tell me about AML compliance"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["response"] == "Fake LLM response"
    assert body["provider"] == "fake"
    assert body["thread_id"] is not None
    # The fake provider should have recorded one call.
    assert len(fake_provider.calls) == 1
    assert fake_provider.calls[0]["json_mode"] is False


@pytest.mark.asyncio
async def test_chat_with_thread(client: AsyncClient):
    resp = await client.post(
        "/chat",
        json={"text": "Follow up question", "thread": "thread-123"},
    )
    assert resp.status_code == 200
    assert resp.json()["thread_id"] == "thread-123"


# ---------------------------------------------------------------------------
# /investigate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_investigate_high_risk(client: AsyncClient, fake_provider: FakeProvider):
    """Investigation with a JSON response indicating High risk."""

    fake_provider._chat_content = json.dumps(
        {
            "risk_level": "High",
            "requires_sar": True,
            "reasoning": "Sanctions match and structuring pattern detected.",
            "recommended_actions": [
                "Freeze transaction",
                "Notify compliance officer",
            ],
        }
    )
    resp = await client.post(
        "/investigate",
        json={
            "transaction": {
                "id": "tx-001",
                "amount": 50000,
                "currency": "USD",
                "sender": "Alice",
                "receiver": "Bob",
                "description": "Large transfer",
            },
            "entities": [
                {"type": "Person", "text": "Alice", "suspicious": True},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["risk_level"] == "High"
    assert body["requires_sar"] is True
    assert "Freeze transaction" in body["recommended_actions"]
    assert body["sar_narrative"] is not None
    # json_mode should have been requested for the investigation call.
    assert fake_provider.calls[0]["json_mode"] is True


@pytest.mark.asyncio
async def test_investigate_low_risk_no_sar(client: AsyncClient):
    """Low-risk investigation should not generate a SAR."""

    import trinity_llm.llm_service as mod

    fake: FakeProvider = mod.llm_service.provider  # type: ignore[assignment]
    fake._chat_content = json.dumps(  # type: ignore[attr-defined]
        {
            "risk_level": "Low",
            "requires_sar": False,
            "reasoning": "No suspicious indicators.",
            "recommended_actions": ["Continue standard monitoring"],
        }
    )
    resp = await client.post(
        "/investigate",
        json={
            "transaction": {
                "id": "tx-002",
                "amount": 100,
                "currency": "USD",
                "sender": "Carol",
                "receiver": "Dave",
                "description": "Small payment",
            },
            "entities": [],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["risk_level"] == "Low"
    assert body["requires_sar"] is False
    assert body["sar_narrative"] is None


@pytest.mark.asyncio
async def test_investigate_regex_fallback(client: AsyncClient):
    """When the LLM returns prose (not JSON), regex fallback parses risk."""

    import trinity_llm.llm_service as mod

    fake: FakeProvider = mod.llm_service.provider  # type: ignore[assignment]
    fake._chat_content = (  # type: ignore[attr-defined]
        "Risk Level: High\n\n"
        "This transaction shows multiple red flags."
    )
    resp = await client.post(
        "/investigate",
        json={
            "transaction": {
                "id": "tx-003",
                "amount": 99999,
                "currency": "USD",
                "sender": "Eve",
                "receiver": "Mallory",
                "description": "Suspicious",
            },
            "entities": [
                {"type": "Person", "text": "Mallory", "suspicious": True},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["risk_level"] == "High"
    assert body["requires_sar"] is True


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_healthy(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["provider"] == "fake"
    assert body["provider_healthy"] is True


@pytest.mark.asyncio
async def test_health_degraded(failing_client: AsyncClient):
    resp = await failing_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["provider_healthy"] is False


# ---------------------------------------------------------------------------
# /providers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_providers_list(client: AsyncClient):
    resp = await client.get("/providers")
    assert resp.status_code == 200
    body = resp.json()
    names = [p["name"] for p in body["providers"]]
    assert "openai" in names
    assert "ollama" in names
    assert "active" in body


# ---------------------------------------------------------------------------
# / (root)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_root(client: AsyncClient):
    resp = await client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "Trinity LLM Service"
    assert body["provider"] == "fake"


# ---------------------------------------------------------------------------
# Error handling — 503 when provider fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_provider_failure_returns_503(failing_client: AsyncClient):
    resp = await failing_client.post("/chat", json={"text": "hello"})
    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_investigate_provider_failure_returns_503(failing_client: AsyncClient):
    resp = await failing_client.post(
        "/investigate",
        json={
            "transaction": {"id": "x", "amount": 1, "sender": "a", "receiver": "b"},
            "entities": [],
        },
    )
    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# /metrics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_endpoint(client: AsyncClient):
    # Make a chat request so there is data.
    await client.post("/chat", json={"text": "hello"})
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "llm_requests_total" in resp.text


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


def test_chat_request_model():
    req = ChatRequest(text="hello")
    assert req.text == "hello"
    assert req.thread is None
    assert req.provider is None


def test_chat_response_model():
    resp = ChatResponse(response="hi", provider="fake")
    assert resp.response == "hi"
    assert resp.provider == "fake"


def test_investigation_request_model():
    req = InvestigationRequest(
        transaction={"id": "1", "amount": 10, "sender": "a", "receiver": "b"},
        entities=[],
    )
    assert req.transaction["id"] == "1"
    assert req.entities == []


def test_investigation_response_model():
    resp = InvestigationResponse(
        risk_level="High",
        requires_sar=True,
        reasoning="test",
        recommended_actions=["act"],
    )
    assert resp.risk_level == "High"
    assert resp.requires_sar is True


# ---------------------------------------------------------------------------
# Direct service tests (no HTTP layer)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_chat_directly(fake_service: TrinityLLMService):
    result = await fake_service.chat("hello")
    assert isinstance(result, ChatResponse)
    assert result.provider == "fake"


@pytest.mark.asyncio
async def test_service_investigate_directly(fake_service: TrinityLLMService):
    fake_service.provider._chat_content = json.dumps(  # type: ignore[attr-defined]
        {
            "risk_level": "Medium",
            "requires_sar": True,
            "reasoning": "Moderate risk.",
            "recommended_actions": ["Monitor"],
        }
    )
    result = await fake_service.investigate_transaction(
        InvestigationRequest(
            transaction={
                "id": "tx-direct",
                "amount": 5000,
                "sender": "x",
                "receiver": "y",
            },
            entities=[],
        )
    )
    assert isinstance(result, InvestigationResponse)
    assert result.risk_level == "Medium"
    assert result.requires_sar is True
