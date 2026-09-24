# Trinity Guard

## The Compliance Trinity

**Orchestrating Python, Go, and Rust for Private, AI-Powered Anti-Money Laundering**

A production-grade, polyglot AML compliance system demo for PyCon Kenya 2026.

---

## Quick Start

```bash
# 1. First-time setup (creates .env, pulls Ollama model)
make setup

# 2. Start all services
make up

# 3. Check service health
make demo-check

# 4. Run the live demo
make demo
```

That's it. The full stack — Go backend, Python NER, Rust ZK, LLM service, PostgreSQL, Redis, Ollama, Prometheus, Grafana — runs via Docker Compose.

---

## Architecture

```
                    Python Orchestrator (Go Backend)
                              |
        +--------------------+--------------------+
        |                    |                    |
   Go Backend         Python NER          Rust ZK Core
   (The Muscle)       (The Brain)         (The Shield)
   
   PostgreSQL          GLINER Model        ZK-SNARKs
   Redis Pub/Sub       FastAPI             PyO3
   Gin Framework       Entity Detection    Poseidon Hash
        |
   LLM Service (AI Investigator)
   OpenAI / Ollama
   SAR Generation
```

### Data Flow

```
Transaction → Go → Python NER → Rust ZK → LLM → SAR
  (Input)    (Fast)  (Smart)     (Secure)  (AI)   (Output)
```

---

## Services

| Service | Language | Port | Role |
|---------|----------|------|------|
| Go Backend | Go 1.27 | 8080 | Transaction orchestrator (The Muscle) |
| Python NER | Python 3.14 | 9000 | Entity resolution with GLINER (The Brain) |
| Rust ZK | Rust 1.95 | 50053 | Zero-knowledge cryptography (The Shield) |
| LLM Service | Python 3.14 | 8081 | SAR generation via OpenAI/Ollama |
| PostgreSQL | - | 5433 | Transaction storage |
| Redis | - | 6380 | NER caching + Go pub/sub |
| Ollama | - | 11434 | Local LLM (self-hosted) |
| Prometheus | - | 9090 | Metrics collection |
| Grafana | - | 3000 | Dashboards |

---

## Project Structure

```
trinity-guard-pycon-kenya-2026/
├── project.md                    # Refactor plan
├── README.md                     # This file
├── Makefile                      # Build/test/deploy targets
├── .env.example                  # Environment configuration template
│
├── contracts/                    # Shared API definitions
│   ├── openapi.yaml              # REST schemas
│   └── proto/trinity.proto       # gRPC definitions
│
├── deploy/                       # Deployment configuration
│   ├── docker-compose.yml        # Full stack orchestration
│   ├── monitoring/               # Prometheus + Grafana configs
│   └── scripts/init-db.sql       # Database schema
│
├── services/                     # The four Trinity services
│   ├── go-backend/               # Go 1.27 — transaction processing
│   ├── python-ner/               # Python 3.14 — GLINER NER
│   ├── rust-zk/                  # Rust — arkworks Groth16 ZK-SNARKs
│   └── llm-service/              # Python 3.14 — OpenAI/Ollama LLM
│
├── demo/                         # Live demo script
│   └── trinity_demo/
│       └── live_demo.py
│
└── .github/workflows/ci.yml      # CI pipeline
```

---

## Configuration

All configuration is via environment variables. Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `ollama` | LLM provider: `ollama` (self-hosted) or `openai` |
| `OLLAMA_MODEL` | `llama3.2` | Ollama model name |
| `OPENAI_API_KEY` | - | Required only if `LLM_PROVIDER=openai` |
| `GLINER_MODEL` | `urchade/gliner_base` | GLINER model (downloads on first run) |
| `DATABASE_URL` | postgres://... | PostgreSQL connection |
| `REDIS_URL` | redis://... | Redis connection |

---

## Makefile Targets

```bash
make setup        # First-time setup
make up           # Start all services
make down         # Stop all services
make demo         # Run the live demo
make demo-check   # Check service health
make test         # Run all tests
make lint         # Lint all services
make build        # Build all Docker images
make logs         # Tail all logs
make db-shell     # PostgreSQL shell
make redis-shell  # Redis CLI
make clean        # Remove all artifacts
```

---

## API Endpoints

### Go Backend (port 8080)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/transactions/process` | Process a transaction |
| GET | `/api/v1/statistics` | Performance metrics |
| GET | `/api/v1/health` | Health check |
| GET | `/metrics` | Prometheus metrics |

### Python NER (port 9000)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/detect` | Detect entities in text |
| POST | `/analyze` | Analyze transaction for compliance |
| GET | `/health` | Health check |
| GET | `/metrics` | Prometheus metrics |

### LLM Service (port 8081)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/chat` | Chat with LLM |
| POST | `/investigate` | Investigate transaction |
| GET | `/providers` | List LLM providers |
| GET | `/health` | Health check |
| GET | `/metrics` | Prometheus metrics |

### Rust ZK (port 50053)

gRPC service — see `contracts/proto/trinity.proto`.

---

## Demo Scenarios

```bash
# Run all scenarios
make demo

# Run specific scenario
python -m trinity_demo.live_demo --scenario sanctions
python -m trinity_demo.live_demo --scenario structuring
python -m trinity_demo.live_demo --scenario high-volume --count 5000
```

1. **Sanctions Evasion** — Detects a sanctioned individual via GLINER NER
2. **Structuring Detection** — Identifies smurfing pattern (sub-$10K transactions)
3. **High-Volume Processing** — Processes N transactions in parallel batches

---

## Development

### Prerequisites

- Python 3.14
- Go 1.27
- Rust 1.95+
- Docker + Docker Compose

### Running Tests

```bash
make test              # All services
make test-rust         # Rust only
make test-go           # Go only
make test-python       # Python only
```

### Linting

```bash
make lint              # All services
```

---

## The Trinity Advantage

- **Right tool for the right job**: Go for speed, Python for intelligence, Rust for security
- **Privacy-preserving**: ZK-SNARKs verify compliance without exposing data
- **Self-hosted**: Ollama for LLM — no external API dependencies
- **Observable**: Prometheus metrics + Grafana dashboards on every service
- **Deployable**: One command — `make up`

---

## Contact

**Maurice Nyanja**
- Twitter: [@nut3case](https://twitter.com/nut3case)
- LinkedIn: [maurice-nyanja](https://linkedin.com/in/maurice-nyanja)
- GitHub: [nutcas3](https://github.com/nutcas3)

---

**Built for PyCon Kenya 2026**

*"Python is not just a language—it's the conductor that makes the entire orchestra of high-performance systems work in harmony."*
