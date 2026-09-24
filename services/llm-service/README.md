# Trinity LLM Service

Pluggable LLM service for Trinity Guard AML compliance automation.
Supports **OpenAI** and **Ollama** (self-hosted, no API key required) via a
provider abstraction. The provider is selected at startup with the
`LLM_PROVIDER` environment variable.

## Features

- **Pluggable providers** — OpenAI (`openai` SDK) and Ollama (REST via `httpx`)
- **No mocks in production** — fails fast with a clear error if the provider
  is misconfigured; no silent fallback
- **Structured JSON output** — both providers support JSON mode for
  deterministic investigation parsing
- **Structured logging** — JSON logs via `structlog`
- **Prometheus metrics** — request counts, investigations, SARs, latency
- **SAR generation** — formal Suspicious Activity Report narratives
- **Python 3.14**

## Architecture

```
trinity_llm/
├── __init__.py          # exports app
├── llm_service.py       # FastAPI app + TrinityLLMService
├── config.py            # pydantic-settings (env config)
├── metrics.py           # Prometheus counters/histograms + /metrics
├── prompts.py           # Jinja2 investigation + SAR templates
└── providers/
    ├── __init__.py      # get_provider() factory
    ├── base.py          # LLMProvider protocol + ChatMessage/LLMResponse
    ├── openai.py        # OpenAI provider (openai.AsyncOpenAI)
    └── ollama.py        # Ollama provider (httpx → /api/chat)
```

## Configuration

All settings are read from environment variables (12-factor config).
See the project `.env.example` for the full list.

| Variable          | Default                        | Description                          |
|-------------------|--------------------------------|--------------------------------------|
| `LLM_PROVIDER`    | `ollama`                       | `openai` or `ollama`                 |
| `OPENAI_API_KEY`  | _(empty)_                      | Required when `LLM_PROVIDER=openai`  |
| `OPENAI_MODEL`    | `gpt-4`                        | OpenAI model name                    |
| `OLLAMA_HOST`     | `http://localhost:11434`       | Ollama server URL                    |
| `OLLAMA_MODEL`    | `llama3.2`                     | Ollama model name                    |
| `HOST`            | `0.0.0.0`                      | Server bind address                  |
| `PORT`            | `8080`                         | Server port                          |

### Provider selection

- **`ollama` (default)** — fully self-hosted, no API key needed. The service
  warns at startup if Ollama is unreachable (it may start later) but does not
  crash.
- **`openai`** — requires `OPENAI_API_KEY`. Crashes at startup with a clear
  error if the key is missing.

## Installation

```bash
uv sync
```

## Usage

```bash
# Default (Ollama, self-hosted)
uv run uvicorn trinity_llm.llm_service:app --host 0.0.0.0 --port 8080

# With OpenAI
LLM_PROVIDER=openai OPENAI_API_KEY=sk-... uv run uvicorn trinity_llm.llm_service:app
```

## API Endpoints

| Method | Path           | Description                              |
|--------|----------------|------------------------------------------|
| GET    | `/`            | Service info                             |
| GET    | `/health`      | Health check (verifies provider health)  |
| POST   | `/chat`        | Chat with the LLM                        |
| POST   | `/investigate` | Investigate a transaction for AML risk   |
| GET    | `/providers`   | List available providers                 |
| GET    | `/metrics`     | Prometheus metrics                       |

### POST /chat

```json
{
  "text": "Explain structuring in money laundering",
  "thread": "optional-thread-id",
  "provider": "ollama"
}
```

### POST /investigate

```json
{
  "transaction": {
    "id": "tx-001",
    "amount": 50000,
    "currency": "USD",
    "sender": "Alice",
    "receiver": "Bob",
    "description": "Large transfer"
  },
  "entities": [
    {"type": "Person", "text": "Bob", "suspicious": true}
  ],
  "context": "Optional additional context"
}
```

Response:

```json
{
  "risk_level": "High",
  "requires_sar": true,
  "reasoning": "Sanctions match and structuring pattern detected.",
  "sar_narrative": "SUSPICIOUS ACTIVITY REPORT ...",
  "recommended_actions": ["Freeze transaction", "Notify compliance officer"],
  "thread_id": "investigation_tx-001"
}
```

## Testing

Tests use a `FakeProvider` (dependency injection) — no real API calls.

```bash
uv run pytest
```

## Docker

```bash
docker build -t trinity-llm-service .
docker run -p 8080:8080 -e LLM_PROVIDER=ollama -e OLLAMA_HOST=http://host.docker.internal:11434 trinity-llm-service
```
