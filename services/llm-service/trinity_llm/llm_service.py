"""
Trinity Guard — LLM Service

AI-powered compliance investigation and SAR generation with pluggable
providers (OpenAI / Ollama).

Endpoints
---------
GET  /            — service info
GET  /health      — health check (verifies provider health)
POST /chat        — chat with the configured LLM
POST /investigate — investigate a transaction for AML risk
GET  /providers   — list available providers
GET  /metrics     — Prometheus metrics
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import settings
from .metrics import (
    llm_investigations_total,
    llm_request_duration_seconds,
    llm_requests_total,
    llm_sars_generated_total,
)
from .prompts import render_investigation_prompt, render_sar_prompt
from .providers import ChatMessage, LLMProvider, get_provider

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
    cache_logger_on_first_use=True,
)
logger = structlog.get_logger(__name__)

VERSION = "2.0.0"


# ---------------------------------------------------------------------------
# Pydantic models — aligned with contracts/openapi.yaml
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """Request body for ``POST /chat`` (OpenAPI: ChatRequest)."""

    text: str
    thread: str | None = None
    provider: str | None = Field(
        default=None,
        description="Override provider (openai | ollama). Defaults to configured provider.",
    )


class ChatResponse(BaseModel):
    """Response for ``POST /chat`` (OpenAPI: ChatResponse)."""

    response: str
    thread_id: str | None = None
    provider: str


class InvestigationRequest(BaseModel):
    """Request body for ``POST /investigate`` (OpenAPI: InvestigationRequest)."""

    transaction: dict[str, Any]
    entities: list[dict[str, Any]]
    context: str | None = None


class InvestigationResponse(BaseModel):
    """Response for ``POST /investigate`` (OpenAPI: InvestigationResponse)."""

    risk_level: str
    requires_sar: bool
    reasoning: str
    sar_narrative: str | None = None
    recommended_actions: list[str]
    thread_id: str | None = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


_VALID_RISK_LEVELS = {"Low", "Medium", "High"}
_RISK_REGEX = re.compile(r"Risk Level:\s*(\w+)", re.IGNORECASE)


class TrinityLLMService:
    """AML compliance investigation service backed by a pluggable LLM provider.

    The provider is injected (not hardcoded) so tests can supply a fake
    implementation without touching the production path.
    """

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider: LLMProvider = provider or get_provider()
        logger.info(
            "llm_service_initialised",
            provider=self.provider.name,
            version=VERSION,
        )

    # -- chat ---------------------------------------------------------------

    async def chat(
        self,
        text: str,
        thread: str | None = None,
    ) -> ChatResponse:
        """Send a free-form chat message to the configured LLM provider."""

        provider_name = self.provider.name
        llm_requests_total.labels(provider=provider_name).inc()
        start = time.perf_counter()

        try:
            messages: list[ChatMessage] = []
            if thread:
                messages.append(
                    ChatMessage(
                        role="system",
                        content=f"Continuing thread: {thread}",
                    )
                )
            messages.append(ChatMessage(role="user", content=text))

            response = await self.provider.chat(messages, json_mode=False)
            return ChatResponse(
                response=response.content,
                thread_id=thread or f"thread_{len(text)}",
                provider=provider_name,
            )
        except Exception as exc:
            logger.error("chat_failed", provider=provider_name, error=str(exc))
            raise HTTPException(
                status_code=503,
                detail=f"LLM provider '{provider_name}' unavailable: {exc}",
            ) from exc
        finally:
            elapsed = time.perf_counter() - start
            llm_request_duration_seconds.labels(provider=provider_name).observe(
                elapsed
            )

    # -- investigation ------------------------------------------------------

    async def investigate_transaction(
        self,
        request: InvestigationRequest,
    ) -> InvestigationResponse:
        """Investigate a transaction for money-laundering risk."""

        transaction = request.transaction
        entities = request.entities
        tx_id = transaction.get("id", "unknown")

        llm_investigations_total.inc()
        logger.info("investigation_started", transaction_id=tx_id)

        provider_name = self.provider.name
        llm_requests_total.labels(provider=provider_name).inc()
        start = time.perf_counter()

        try:
            prompt = render_investigation_prompt(
                transaction=transaction,
                entities=entities,
                context=request.context,
            )
            messages = [
                ChatMessage(
                    role="system",
                    content=(
                        "You are an AML compliance analyst. "
                        "Respond only with the requested JSON."
                    ),
                ),
                ChatMessage(role="user", content=prompt),
            ]

            response = await self.provider.chat(messages, json_mode=True)
            result = self._parse_investigation_response(
                response.content, transaction, entities, tx_id
            )

            if result.requires_sar and result.sar_narrative is None:
                result = await self._generate_sar(
                    result, transaction, entities, tx_id
                )

            logger.info(
                "investigation_completed",
                transaction_id=tx_id,
                risk_level=result.risk_level,
                requires_sar=result.requires_sar,
            )
            return result
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(
                "investigation_failed",
                transaction_id=tx_id,
                error=str(exc),
            )
            raise HTTPException(
                status_code=503,
                detail=f"LLM provider '{provider_name}' unavailable: {exc}",
            ) from exc
        finally:
            elapsed = time.perf_counter() - start
            llm_request_duration_seconds.labels(provider=provider_name).observe(
                elapsed
            )

    # -- parsing ------------------------------------------------------------

    def _parse_investigation_response(
        self,
        llm_response: str,
        transaction: dict[str, Any],
        entities: list[dict[str, Any]],
        tx_id: str,
    ) -> InvestigationResponse:
        """Parse the LLM investigation response into structured data.

        Primary path: parse JSON ``{"risk_level", "requires_sar", ...}``.
        Fallback: regex ``Risk Level:\\s*(\\w+)`` for non-JSON responses.
        """

        risk_level = "Low"
        requires_sar = False
        reasoning = llm_response
        recommended_actions: list[str] = []

        # --- Primary: JSON parse ---
        parsed: dict[str, Any] | None = None
        try:
            parsed = json.loads(llm_response)
        except (json.JSONDecodeError, TypeError):
            # Try to extract a JSON object from within the text.
            match = re.search(r"\{.*\}", llm_response, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except json.JSONDecodeError:
                    parsed = None

        if isinstance(parsed, dict):
            raw_risk = str(parsed.get("risk_level", "")).strip()
            if raw_risk.capitalize() in _VALID_RISK_LEVELS:
                risk_level = raw_risk.capitalize()
            requires_sar = bool(parsed.get("requires_sar", False))
            reasoning = str(parsed.get("reasoning", llm_response))
            actions = parsed.get("recommended_actions", [])
            if isinstance(actions, list):
                recommended_actions = [str(a) for a in actions]
        else:
            # --- Fallback: regex ---
            logger.warning(
                "investigation_json_parse_failed",
                transaction_id=tx_id,
                fallback="regex",
            )
            match = _RISK_REGEX.search(llm_response)
            if match:
                risk_level = match.group(1).capitalize()
            requires_sar = risk_level in {"Medium", "High"}
            reasoning = llm_response

        if not recommended_actions:
            recommended_actions = self._default_recommendations(risk_level)

        sar_narrative: str | None = None
        if requires_sar:
            sar_narrative = self._build_sar_narrative(
                transaction, entities, reasoning
            )
            llm_sars_generated_total.inc()

        return InvestigationResponse(
            risk_level=risk_level,
            requires_sar=requires_sar,
            reasoning=reasoning,
            sar_narrative=sar_narrative,
            recommended_actions=recommended_actions,
            thread_id=f"investigation_{tx_id}",
        )

    # -- SAR generation -----------------------------------------------------

    async def _generate_sar(
        self,
        result: InvestigationResponse,
        transaction: dict[str, Any],
        entities: list[dict[str, Any]],
        tx_id: str,
    ) -> InvestigationResponse:
        """Ask the LLM to generate a formal SAR narrative."""

        try:
            prompt = render_sar_prompt(
                investigation_results=result.reasoning,
                transaction=transaction,
                entities=entities,
            )
            messages = [
                ChatMessage(
                    role="system",
                    content=(
                        "You are a compliance officer drafting a SAR. "
                        "Respond only with the requested JSON."
                    ),
                ),
                ChatMessage(role="user", content=prompt),
            ]
            response = await self.provider.chat(messages, json_mode=True)

            sar_text: str | None = None
            try:
                parsed = json.loads(response.content)
                sar_text = parsed.get("sar_narrative")
            except (json.JSONDecodeError, TypeError):
                match = re.search(r"\{.*\}", response.content, re.DOTALL)
                if match:
                    try:
                        parsed = json.loads(match.group(0))
                        sar_text = parsed.get("sar_narrative")
                    except json.JSONDecodeError:
                        sar_text = None

            if not sar_text:
                sar_text = response.content

            llm_sars_generated_total.inc()
            return result.model_copy(update={"sar_narrative": sar_text})
        except Exception as exc:
            logger.warning(
                "sar_generation_failed",
                transaction_id=tx_id,
                error=str(exc),
                fallback="template",
            )
            sar = self._build_sar_narrative(transaction, entities, result.reasoning)
            llm_sars_generated_total.inc()
            return result.model_copy(update={"sar_narrative": sar})

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _default_recommendations(risk_level: str) -> list[str]:
        if risk_level == "High":
            return [
                "Immediately freeze transaction pending investigation",
                "Notify compliance officer within 24 hours",
                "File SAR with FinCEN within 30 days",
                "Review all related transactions from same parties",
                "Consider enhanced due diligence",
            ]
        if risk_level == "Medium":
            return [
                "Flag for enhanced monitoring",
                "Request additional documentation",
                "Review transaction history for patterns",
                "Consider SAR filing based on additional findings",
            ]
        return ["Continue standard monitoring"]

    @staticmethod
    def _build_sar_narrative(
        transaction: dict[str, Any],
        entities: list[dict[str, Any]],
        reasoning: str,
    ) -> str:
        """Build a SAR narrative template (used as a fallback)."""

        suspicious = [e for e in entities if e.get("suspicious")]
        entity_lines = "\n".join(
            f"- {e.get('text', 'Unknown')} ({e.get('type', 'Unknown')})"
            for e in suspicious
        ) or "None identified"
        return (
            "SUSPICIOUS ACTIVITY REPORT\n\n"
            "Transaction Summary:\n"
            f"- ID: {transaction.get('id')}\n"
            f"- Amount: {transaction.get('currency', 'USD')} "
            f"{float(transaction.get('amount', 0)):,.2f}\n"
            f"- Sender: {transaction.get('sender')}\n"
            f"- Receiver: {transaction.get('receiver')}\n"
            f"- Description: {transaction.get('description')}\n\n"
            "Suspicious Activity:\n"
            f"{reasoning}\n\n"
            "Entities Involved:\n"
            f"{entity_lines}\n\n"
            "Regulatory Concerns:\n"
            "- Potential sanctions violations\n"
            "- High-value transaction requiring reporting\n"
            "- Suspicious entity involvement\n\n"
            "Recommended Actions:\n"
            "- Immediate compliance review\n"
            "- Consider transaction freeze\n"
            "- File SAR within regulatory timeframe\n"
            "- Enhanced monitoring of related parties"
        )


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Trinity LLM Service",
    description=(
        "Pluggable LLM service (OpenAI / Ollama) for Trinity Guard "
        "compliance automation — investigation and SAR generation."
    ),
    version=VERSION,
)

# Initialise the service with the configured provider.
llm_service = TrinityLLMService()


@app.on_event("startup")
async def _startup() -> None:
    """Validate the configured provider at startup."""

    provider_name = settings.llm_provider
    logger.info("startup_validating_provider", provider=provider_name)

    if provider_name == "openai":
        # OpenAIProvider already raised in get_provider() if no key —
        # this is a belt-and-braces check.
        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY required when LLM_PROVIDER=openai."
            )
    elif provider_name == "ollama":
        healthy = await llm_service.provider.health()
        if not healthy:
            logger.warning(
                "ollama_unreachable_at_startup",
                host=settings.ollama_host,
                msg="Ollama may start after this service — continuing.",
            )
        else:
            logger.info("ollama_healthy_at_startup", host=settings.ollama_host)


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "service": "Trinity LLM Service",
        "status": "running",
        "version": VERSION,
        "provider": llm_service.provider.name,
        "endpoints": [
            "/health",
            "/chat",
            "/investigate",
            "/providers",
            "/metrics",
        ],
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    healthy = await llm_service.provider.health()
    status = "healthy" if healthy else "degraded"
    return {
        "status": status,
        "version": VERSION,
        "provider": llm_service.provider.name,
        "provider_healthy": healthy,
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Chat with the configured LLM provider."""

    return await llm_service.chat(request.text, request.thread)


@app.post("/investigate", response_model=InvestigationResponse)
async def investigate(request: InvestigationRequest) -> InvestigationResponse:
    """Investigate a transaction for AML compliance risk."""

    return await llm_service.investigate_transaction(request)


@app.get("/providers")
async def list_providers() -> dict[str, Any]:
    """List available LLM providers and the active one."""

    return {
        "providers": [
            {"name": "openai", "available": bool(settings.openai_api_key)},
            {
                "name": "ollama",
                "available": True,
                "host": settings.ollama_host,
                "model": settings.ollama_model,
            },
        ],
        "default": settings.llm_provider,
        "active": llm_service.provider.name,
    }


@app.get("/metrics")
async def prometheus_metrics() -> Any:
    """Prometheus metrics endpoint."""

    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    from starlette.responses import Response

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    logger.info(
        "starting_trinity_llm_service",
        host=settings.host,
        port=settings.port,
        provider=settings.llm_provider,
    )
    uvicorn.run(app, host=settings.host, port=settings.port)
