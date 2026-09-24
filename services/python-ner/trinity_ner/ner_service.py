"""
Trinity Guard — Python NER Service (The Brain).

Entity recognition powered by GLINER with sanctions-list matching,
Redis caching, Prometheus metrics, and structured JSON logging.

Production refactor (Phase 3):
- No mock fallback — GLINER is required and fails loudly if unavailable.
- Entity type is never mutated; suspicious flag + sanctions_matches carry risk.
- Redis caches GLINER predictions and sanctions lookups (degrades gracefully).
- /analyze endpoint is fully typed via Pydantic request/response models.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, List, Optional, Protocol

import redis.asyncio as aioredis
import structlog
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Response
from pydantic import BaseModel

from .config import settings
from .metrics import (
    metrics_response,
    ner_entities_detected_total,
    ner_inference_duration_seconds,
    ner_requests_total,
    ner_suspicious_found_total,
)

# ---------------------------------------------------------------------------
# Structured logging (JSON)
# ---------------------------------------------------------------------------

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# GLINER model protocol (for dependency injection in tests)
# ---------------------------------------------------------------------------


class GLiNERModel(Protocol):
    """Minimal protocol a GLINER-compatible model must satisfy."""

    def predict_entities(
        self, text: str, labels: List[str], **kwargs: Any
    ) -> List[dict]: ...


# ---------------------------------------------------------------------------
# Pydantic models — aligned with contracts/openapi.yaml
# ---------------------------------------------------------------------------


class DetectRequest(BaseModel):
    text: str
    labels: Optional[List[str]] = None


class SanctionMatch(BaseModel):
    sanction_id: str
    name: str
    matched_alias: Optional[str] = None
    similarity: float
    risk_level: str
    match_type: str  # "direct" or "alias"


class Entity(BaseModel):
    type: str
    text: str
    suspicious: bool = False
    sanctions_matches: List[SanctionMatch] = []


class DetectResponse(BaseModel):
    entities: List[Entity]


class TransactionAnalysisRequest(BaseModel):
    id: Optional[str] = None
    description: str = ""
    sender: str = ""
    receiver: str = ""
    amount: Optional[float] = None
    currency: str = "USD"


class TransactionAnalysisResponse(BaseModel):
    transaction_id: Optional[str] = None
    entities: List[Entity]
    suspicious_count: int
    is_suspicious: bool
    risk_level: str  # "LOW", "MEDIUM", "HIGH"


class MarbleNERService:
    """GLINER-backed NER service with sanctions matching and Redis caching."""

    def __init__(self, model: Optional[GLiNERModel] = None) -> None:
        logger.info("ner_service.initializing")

        # GLINER model — required, no mock fallback.
        if model is not None:
            # Dependency-injected model (used by tests).
            self.model: Optional[GLiNERModel] = model
            logger.info("ner_service.model_loaded", source="injected")
        else:
            self.model = self._load_gliner_model()

        # Default labels parsed from config.
        self.default_labels: List[str] = [
            label.strip() for label in settings.gliner_labels.split(",") if label.strip()
        ]

        # In-memory sanctions database (always available).
        self.sanctions_db: List[dict] = self._load_sanctions_database()

        # Redis client (optional — degrades gracefully if unavailable).
        self.redis: Optional[aioredis.Redis] = None

        logger.info(
            "ner_service.ready",
            model=settings.gliner_model,
            labels=self.default_labels,
            sanctions_count=len(self.sanctions_db),
        )

    # -- GLINER -----------------------------------------------------------

    @staticmethod
    def _load_gliner_model() -> GLiNERModel:
        """Load the GLINER model, failing loudly if unavailable."""
        try:
            from gliner import GLiNER  # imported lazily so tests can inject a double
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "GLINER model required — install: pip install gliner"
            ) from exc

        try:
            model = GLiNER.from_pretrained(settings.gliner_model)
            logger.info(
                "ner_service.model_loaded",
                source="huggingface",
                model=settings.gliner_model,
                cache_dir=settings.gliner_cache_dir,
            )
            return model
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load GLINER model '{settings.gliner_model}': {exc}"
            ) from exc

    # -- Redis ------------------------------------------------------------

    async def connect_redis(self) -> None:
        """Connect to Redis. Logs a warning and continues without cache on failure."""
        try:
            self.redis = aioredis.from_url(
                settings.redis_url, decode_responses=True, socket_timeout=2
            )
            await self.redis.ping()
            logger.info("ner_service.redis.connected", url=settings.redis_url)
        except Exception as exc:
            logger.warning(
                "ner_service.redis.unavailable",
                url=settings.redis_url,
                error=str(exc),
            )
            self.redis = None

    async def close_redis(self) -> None:
        if self.redis is not None:
            await self.redis.close()
            self.redis = None


    def _load_sanctions_database(self) -> List[dict]:
        """Load the in-memory sanctions list. Also tries Redis if available."""
        return [
            {
                "id": "sanction_001",
                "name": "M. Emmanuel",
                "aliases": ["Maurice Emmanuel", "M. Nyanja", "Maurice Nyanja"],
                "risk_level": "HIGH",
                "sanctions": ["Asset Freeze", "Travel Ban"],
                "jurisdictions": ["US", "EU", "UK"],
            },
            {
                "id": "sanction_002",
                "name": "Robert Mugabe",
                "aliases": ["Bob Mugabe", "R. Mugabe"],
                "risk_level": "HIGH",
                "sanctions": ["Asset Freeze"],
                "jurisdictions": ["US", "EU"],
            },
            {
                "id": "sanction_003",
                "name": "Martin Finnigan",
                "aliases": ["Martin Finn", "M. Finnigan"],
                "risk_level": "HIGH",
                "sanctions": ["Asset Freeze"],
                "jurisdictions": ["US", "EU"],
            },
            {
                "id": "sanction_004",
                "name": "Moneycorp",
                "aliases": ["Money Corp", "Moneycorp Ltd"],
                "risk_level": "HIGH",
                "sanctions": ["Monitoring"],
                "jurisdictions": ["US"],
            },
        ]

    async def _load_sanctions_from_redis(self) -> None:
        """Optionally enrich the in-memory list from Redis (best-effort)."""
        if self.redis is None:
            return
        try:
            raw = await self.redis.get("trinity:sanctions_db")
            if raw:
                loaded = json.loads(raw)
                if isinstance(loaded, list) and loaded:
                    self.sanctions_db = loaded
                    logger.info(
                        "ner_service.sanctions.loaded_from_redis",
                        count=len(self.sanctions_db),
                    )
        except Exception as exc:
            logger.warning("ner_service.sanctions.redis_load_failed", error=str(exc))


    async def detect_entities(
        self, text: str, labels: Optional[List[str]] = None
    ) -> List[Entity]:
        if labels is None:
            labels = self.default_labels

        logger.info("ner_service.detect.start", text_len=len(text), labels=labels)

        # Redis cache lookup for GLINER predictions.
        cache_key = self._prediction_cache_key(text, labels)
        cached = await self._cache_get(cache_key)
        if cached is not None:
            entities = [Entity(**e) for e in cached]
            logger.info("ner_service.detect.cache_hit", entities=len(entities))
            enhanced = self._enhance_entities_with_sanctions(entities)
            return enhanced

        # GLINER inference.
        start = time.perf_counter()
        entities = self._run_gliner(text, labels)
        duration = time.perf_counter() - start
        ner_inference_duration_seconds.observe(duration)

        # Cache the raw GLINER output (before sanctions enhancement).
        await self._cache_set(
            cache_key, [e.model_dump() for e in entities], ttl=3600
        )

        # Sanctions enhancement.
        enhanced = self._enhance_entities_with_sanctions(entities)

        ner_entities_detected_total.inc(len(enhanced))
        suspicious = [e for e in enhanced if e.suspicious]
        if suspicious:
            ner_suspicious_found_total.inc(len(suspicious))

        logger.info(
            "ner_service.detect.complete",
            entities=len(enhanced),
            suspicious=len(suspicious),
            duration_ms=round(duration * 1000, 2),
        )
        return enhanced

    def _run_gliner(self, text: str, labels: List[str]) -> List[Entity]:
        """Run GLINER inference. Raises if the model is not loaded."""
        if self.model is None:
            raise RuntimeError("GLINER model is not loaded")

        results = self.model.predict_entities(text, labels)
        entities: List[Entity] = []
        for result in results:
            entity = Entity(type=result["label"], text=result["text"])
            entities.append(entity)
            logger.info(
                "ner_service.entity.detected",
                text=entity.text,
                type=entity.type,
            )
        return entities


    def _enhance_entities_with_sanctions(self, entities: List[Entity]) -> List[Entity]:
        """Set suspicious flag + populate sanctions_matches. Never mutates type."""
        for entity in entities:
            matches = self._match_sanctions(entity.text)
            if matches:
                entity.suspicious = True
                entity.sanctions_matches = matches
                logger.warning(
                    "ner_service.entity.suspicious",
                    text=entity.text,
                    matches=len(matches),
                )
        return entities

    def _match_sanctions(self, entity_text: str) -> List[SanctionMatch]:
        matches: List[SanctionMatch] = []
        entity_lower = entity_text.lower()

        for sanction in self.sanctions_db:
            name_lower = sanction["name"].lower()
            sim = self._calculate_similarity(entity_lower, name_lower)
            if sim > 0.8:
                matches.append(
                    SanctionMatch(
                        sanction_id=sanction["id"],
                        name=sanction["name"],
                        similarity=round(sim, 4),
                        risk_level=sanction["risk_level"],
                        match_type="direct",
                    )
                )
                continue

            for alias in sanction.get("aliases", []):
                sim = self._calculate_similarity(entity_lower, alias.lower())
                if sim > 0.8:
                    matches.append(
                        SanctionMatch(
                            sanction_id=sanction["id"],
                            name=sanction["name"],
                            matched_alias=alias,
                            similarity=round(sim, 4),
                            risk_level=sanction["risk_level"],
                            match_type="alias",
                        )
                    )
                    break

        return matches

    @staticmethod
    def _calculate_similarity(text1: str, text2: str) -> float:
        if text1 == text2:
            return 1.0
        if text1 in text2 or text2 in text1:
            return 0.9
        words1 = set(text1.split())
        words2 = set(text2.split())
        if not words1 or not words2:
            return 0.0
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        return intersection / union if union > 0 else 0.0

    @staticmethod
    def _prediction_cache_key(text: str, labels: List[str]) -> str:
        digest = hashlib.sha256(
            f"{text}|{','.join(labels)}".encode("utf-8")
        ).hexdigest()
        return f"trinity:ner:pred:{digest}"

    async def _cache_get(self, key: str) -> Optional[Any]:
        if self.redis is None:
            return None
        try:
            raw = await self.redis.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.warning("ner_service.cache.get_failed", key=key, error=str(exc))
            return None

    async def _cache_set(self, key: str, value: Any, ttl: int = 3600) -> None:
        if self.redis is None:
            return
        try:
            await self.redis.set(key, json.dumps(value), ex=ttl)
        except Exception as exc:
            logger.warning("ner_service.cache.set_failed", key=key, error=str(exc))


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Trinity Guard NER Service",
    description="GLINER-based entity recognition with sanctions matching — The Brain.",
    version="2.0.0",
)

# Service instance — initialized on startup so tests can override it.
ner_service: Optional[MarbleNERService] = None


def get_ner_service() -> MarbleNERService:
    """Dependency accessor. Raises if the service is not initialized."""
    if ner_service is None:
        raise RuntimeError("NER service not initialized")
    return ner_service


@app.on_event("startup")
async def _startup() -> None:
    global ner_service
    # Allow tests / external code to pre-inject a service instance.
    if ner_service is None:
        ner_service = MarbleNERService()
        await ner_service.connect_redis()
        await ner_service._load_sanctions_from_redis()


@app.on_event("shutdown")
async def _shutdown() -> None:
    if ner_service is not None:
        await ner_service.close_redis()


@app.get("/")
async def root() -> dict:
    return {
        "service": "Trinity Guard NER Service",
        "status": "running",
        "model": settings.gliner_model,
        "integration": "Trinity Guard",
    }


@app.get("/health")
async def health(service: MarbleNERService = Depends(get_ner_service)) -> dict:
    return {
        "status": "healthy",
        "gliner_available": service.model is not None,
        "model_loaded": service.model is not None,
        "redis_connected": service.redis is not None,
        "sanctions_db_size": len(service.sanctions_db),
        "version": "2.0.0",
    }


@app.post("/detect", response_model=DetectResponse)
async def detect_entities(
    request: DetectRequest,
    service: MarbleNERService = Depends(get_ner_service),
) -> DetectResponse:
    ner_requests_total.labels(endpoint="detect").inc()
    try:
        entities = await service.detect_entities(request.text, request.labels)
        return DetectResponse(entities=entities)
    except Exception as exc:
        logger.error("ner_service.detect.failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/analyze", response_model=TransactionAnalysisResponse)
async def analyze_transaction(
    request: TransactionAnalysisRequest,
    service: MarbleNERService = Depends(get_ner_service),
) -> TransactionAnalysisResponse:
    ner_requests_total.labels(endpoint="analyze").inc()
    try:
        text = f"{request.description} {request.sender} {request.receiver}"
        entities = await service.detect_entities(text, service.default_labels)

        suspicious_count = sum(1 for e in entities if e.suspicious)
        is_suspicious = suspicious_count > 0

        if suspicious_count >= 2:
            risk_level = "HIGH"
        elif suspicious_count == 1:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        return TransactionAnalysisResponse(
            transaction_id=request.id,
            entities=entities,
            suspicious_count=suspicious_count,
            is_suspicious=is_suspicious,
            risk_level=risk_level,
        )
    except Exception as exc:
        logger.error("ner_service.analyze.failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/metrics")
async def metrics() -> Response:
    body, content_type = metrics_response()
    return Response(content=body, media_type=content_type)


if __name__ == "__main__":
    logger.info(
        "ner_service.starting",
        host=settings.host,
        port=settings.port,
        model=settings.gliner_model,
    )
    uvicorn.run(app, host=settings.host, port=settings.port)
