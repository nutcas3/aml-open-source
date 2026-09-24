# Trinity Guard NER Service

**The Brain** — GLINER-based entity recognition with sanctions-list matching for the Trinity Guard AML compliance stack.

Built on Python 3.14, FastAPI, and the [GLINER](https://github.com/urchade/GLiNER) zero-shot NER model.

## Features

- **GLINER entity recognition** — zero-shot NER with the `urchade/gliner_base` model (downloaded on first startup, cached in a Docker volume).
- **Sanctions matching** — entities are checked against an in-memory sanctions database (enrichable from Redis) with direct + alias matching.
- **Structured suspicious flag** — entities carry a `suspicious: bool` flag and a `sanctions_matches` list (entity `type` is never mutated). This is the contract the Go backend relies on.
- **Redis caching** — GLINER predictions are cached by SHA-256(text) hash; sanctions lookups can be loaded from Redis. Degrades gracefully if Redis is unavailable.
- **Prometheus metrics** — request count, entities detected, suspicious found, and inference duration exposed at `/metrics`.
- **Structured logging** — JSON logs via `structlog`.
- **Typed `/analyze` endpoint** — Pydantic request/response models aligned with `contracts/openapi.yaml`.
- **No mock fallback** — GLINER is required. The service fails loudly at startup if the model can't load.

## API Endpoints

| Method | Path         | Description                                      |
|--------|--------------|--------------------------------------------------|
| GET    | `/`          | Service info                                     |
| GET    | `/health`    | Health check (model + Redis + sanctions status)  |
| GET    | `/metrics`   | Prometheus metrics                               |
| POST   | `/detect`    | Detect entities in text                          |
| POST   | `/analyze`   | Analyze a transaction for compliance             |

### `/detect`

```bash
curl -X POST http://localhost:9000/detect \
  -H "Content-Type: application/json" \
  -d '{"text": "M. Emmanuel sent funds to Moneycorp in Kenya."}'
```

```json
{
  "entities": [
    {
      "type": "Person",
      "text": "M. Emmanuel",
      "suspicious": true,
      "sanctions_matches": [
        {
          "sanction_id": "sanction_001",
          "name": "M. Emmanuel",
          "matched_alias": null,
          "similarity": 1.0,
          "risk_level": "HIGH",
          "match_type": "direct"
        }
      ]
    },
    {
      "type": "Company",
      "text": "Moneycorp",
      "suspicious": true,
      "sanctions_matches": [ /* ... */ ]
    },
    {
      "type": "Country",
      "text": "Kenya",
      "suspicious": false,
      "sanctions_matches": []
    }
  ]
}
```

### `/analyze`

```bash
curl -X POST http://localhost:9000/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "id": "tx-002",
    "description": "money laundering scheme",
    "sender": "M. Emmanuel",
    "receiver": "Moneycorp",
    "amount": 999999.00,
    "currency": "USD"
  }'
```

```json
{
  "transaction_id": "tx-002",
  "entities": [ /* ... */ ],
  "suspicious_count": 2,
  "is_suspicious": true,
  "risk_level": "HIGH"
}
```

Risk level is derived from the suspicious entity count: `0 → LOW`, `1 → MEDIUM`, `≥2 → HIGH`.

## Configuration

All config is loaded from environment variables (12-factor). See `trinity_ner/config.py`.

| Env var           | Default                  | Description                                      |
|-------------------|--------------------------|--------------------------------------------------|
| `HOST`            | `0.0.0.0`                | Bind host                                        |
| `PORT`            | `9000`                   | Bind port                                        |
| `GLINER_MODEL`    | `urchade/gliner_base`    | HuggingFace model ID                             |
| `GLINER_CACHE_DIR`| `/app/models`            | Model cache directory (Docker volume)            |
| `GLINER_LABELS`   | `Person,Company,Country,Illegal Activity` | Comma-separated default labels |
| `REDIS_URL`       | `redis://localhost:6379` | Redis connection string (caching)                |

A `.env` file is automatically loaded when present.

## Installation

```bash
uv pip install -e .
```

## Usage

```bash
uv run uvicorn trinity_ner.ner_service:app --reload --host 0.0.0.0 --port 9000
```

On first startup the GLINER model is downloaded from HuggingFace and cached in `GLINER_CACHE_DIR`.

## Testing

Tests use `httpx.AsyncClient` against the FastAPI ASGI app with a **fake GLINER model** injected via FastAPI dependency injection — no real model is loaded.

```bash
uv pip install -e ".[dev]"
pytest
```

## Docker

```bash
docker build -t trinity-ner .
docker run -p 9000:9000 -v gliner_models:/app/models trinity-ner
```

The `/app/models` volume caches the GLINER model across container restarts.

## Architecture

```
trinity_ner/
├── __init__.py        # exports app
├── ner_service.py     # FastAPI app + MarbleNERService (GLINER + sanctions + Redis)
├── config.py          # pydantic-settings config from env vars
└── metrics.py         # Prometheus metric definitions + /metrics helper
tests/
└── test_ner_service.py
```

## Contract

This service conforms to the shared schemas in [`contracts/openapi.yaml`](../../contracts/openapi.yaml) — specifically `Entity`, `SanctionMatch`, `DetectRequest`, `DetectResponse`, `TransactionAnalysisRequest`, and `TransactionAnalysisResponse`.
