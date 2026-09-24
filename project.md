# Trinity Guard — Production Refactor Plan

**Status:** Implemented
**Created:** 2026-09-01
**Completed:** 2026-09-02
**Target:** PyCon Kenya 2026 demo — deployable, production-grade polyglot AML stack

---

## Pinned Versions

| Toolchain | Version | Notes |
|-----------|---------|-------|
| Python | 3.14 | Stable since Oct 2025 (currently 3.14.7). Used for NER, LLM, demo. |
| Go | 1.27.0 | Released Aug 19, 2026. Used for backend orchestrator. |
| Rust | 1.95+ | Stable. Used for ZK cryptography service. |
| Docker | Compose v2 | For local orchestration. |

---

## Decisions

1. **All 4 services + demo** refactored to production-grade
2. **Real implementations only** — no mock fallbacks in production path. GLINER, arkworks ZK all required. Test doubles via dependency injection in tests only.
3. **Real arkworks Groth16** ZK-SNARKs in Rust (not mocks)
4. **Full infra** — CI, Prometheus/Grafana monitoring, `.env.example`, Makefile, structured logging, health checks
5. **Python 3.14** stable, **Go 1.27.0** latest
6. **LLM provider: pluggable** — supports OpenAI *and* local LLMs via Ollama. Runs fully self-hosted without an OpenAI key. Provider selected via `LLM_PROVIDER` env var (`openai` or `ollama`).
7. **GLINER model: runtime download** — downloaded on first startup, cached in a Docker volume (`gliner_models`). Smaller image, cached across restarts.
8. **Firebase: replaced with Redis pub/sub** — no Google Cloud dependency. Redis (already in stack) handles real-time notifications. Go backend publishes transaction events to Redis channels.

---

## Guiding Principles

1. **No mocks** — every service runs for real or fails loudly with a clear error
2. **Polyglot contracts** — shared protobuf/OpenAPI schemas so Go, Python, Rust agree on types
3. **12-factor config** — all config via env vars, `.env.example` documents every one
4. **Observable** — structured logging (JSON), Prometheus metrics on every service, health checks
5. **Deployable** — `make setup && make up && make demo` brings up the full self-hosted stack

---

## Target Directory Structure

```
trinity-guard-pycon-kenya-2026/
├── project.md                    # This file
├── README.md                     # Updated quickstart
├── Makefile                      # Build/test/deploy targets
├── .env.example                  # All env vars documented
├── .gitignore                    # Updated
│
├── contracts/                    # Shared API definitions (NEW)
│   ├── openapi.yaml              # REST schemas for all HTTP endpoints
│   └── proto/
│       └── trinity.proto         # gRPC definitions for inter-service calls
│
├── deploy/                       # All deployment config (NEW)
│   ├── docker-compose.yml        # Fixed, includes Ollama for local LLM
│   ├── monitoring/
│   │   ├── prometheus.yml
│   │   └── grafana/
│   │       ├── dashboards/
│   │       │   └── trinity-overview.json
│   │       └── datasources/
│   │           └── prometheus.yml
│   └── scripts/
│       └── init-db.sql           # Moved from scripts/, aligned with Go
│
├── services/                     # Renamed from trinity-code-samples
│   ├── go-backend/               # The Muscle — Go 1.27.0
│   │   ├── main.go
│   │   ├── go.mod                # No Firebase deps; adds redis/go-client
│   │   ├── go.sum
│   │   ├── Dockerfile
│   │   └── README.md
│   │
│   ├── python-ner/               # The Brain — Python 3.14 + GLINER
│   │   ├── trinity_ner/
│   │   │   ├── __init__.py
│   │   │   ├── ner_service.py
│   │   │   ├── config.py         # NEW — settings via env
│   │   │   └── metrics.py        # NEW — Prometheus
│   │   ├── tests/
│   │   │   └── test_ner_service.py
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   └── README.md
│   │
│   ├── rust-zk/                  # The Shield — Rust + arkworks Groth16
│   │   ├── src/
│   │   │   ├── lib.rs            # PyO3 module + ZK functions
│   │   │   ├── main.rs           # tonic gRPC server
│   │   │   ├── circuit.rs        # NEW — ComplianceCircuit
│   │   │   ├── poseidon.rs       # NEW — real Poseidon hash
│   │   │   └── merkle.rs         # NEW — real Merkle verification
│   │   ├── tests/
│   │   ├── Cargo.toml
│   │   ├── Cargo.lock
│   │   ├── Dockerfile
│   │   └── README.md
│   │
│   └── llm-service/              # AI Investigator — Python 3.14 + OpenAI/Ollama
│       ├── trinity_llm/
│       │   ├── __init__.py
│       │   ├── llm_service.py    # FastAPI app
│       │   ├── config.py         # NEW — settings via env
│       │   ├── metrics.py        # NEW — Prometheus
│       │   ├── prompts.py        # NEW — prompt templates
│       │   └── providers/        # NEW — pluggable LLM providers
│       │       ├── __init__.py
│       │       ├── base.py       # LLMProvider protocol
│       │       ├── openai.py     # OpenAI implementation
│       │       └── ollama.py     # Ollama (local LLM) implementation
│       ├── tests/
│       │   └── test_llm_service.py
│       ├── pyproject.toml
│       ├── Dockerfile
│       └── README.md
│
├── demo/                         # Renamed from demo-scripts
│   ├── trinity_demo/
│   │   ├── __init__.py
│   │   └── live_demo.py
│   ├── pyproject.toml
│   └── README.md
│
└── .github/                      # CI (NEW)
    └── workflows/
        └── ci.yml                # Lint + test + build all services
```

---

## Phase 0: Shared Contracts & Scaffolding

**Goal:** Fix cross-service contract bugs and lay infra foundation.

### 0.1 Define shared API contracts
- `contracts/openapi.yaml` — REST schemas for NER `/detect`, LLM `/chat` + `/investigate`, Go `/process` + `/statistics` + `/health`
- `contracts/proto/trinity.proto` — gRPC for Go ↔ NER ↔ ZK calls
- Single source of truth; all services conform

### 0.2 Restructure project
- Rename `trinity-code-samples/` → `services/`
- Rename `demo-scripts/` → `demo/`
- Create `deploy/`, `contracts/`, `.github/` directories
- Move `docker-compose.yml` → `deploy/docker-compose.yml`
- Move `scripts/init-db.sql` → `deploy/scripts/init-db.sql`

### 0.3 `.env.example`
Every env var documented:
- `DATABASE_URL` — PostgreSQL connection string
- `NER_ENDPOINT`, `LLM_ENDPOINT`, `ZK_ENDPOINT` — inter-service URLs
- `LLM_PROVIDER` — `openai` or `ollama` (defaults to `ollama` for self-hosted)
- `OPENAI_API_KEY` — required only if `LLM_PROVIDER=openai`
- `OLLAMA_HOST` — Ollama endpoint (defaults to `http://localhost:11434`)
- `OLLAMA_MODEL` — Ollama model name (defaults to `llama3.2`)
- `GLINER_MODEL` — defaults to `urchade/gliner_base`
- `GLINER_CACHE_DIR` — model cache directory (defaults to `/app/models`)
- `REDIS_URL` — for NER caching + Go pub/sub
- `RUST_LOG` — log level for Rust service
- `PORT_GO`, `PORT_NER`, `PORT_LLM`, `PORT_ZK` — per-service ports
- `PROMETHEUS_*`, `GRAFANA_ADMIN_PASSWORD`

### 0.4 Fix `init-db.sql`
- Align `transactions` table columns with Go's `storeTransaction` query
- Use `transaction_id VARCHAR` as conflict column (not UUID `id`)
- Verify all `CREATE TABLE IF NOT EXISTS` guards
- Move to `deploy/scripts/`

---

## Phase 1: Rust ZK Service — Real arkworks Groth16

**Heaviest change.** Replace all mock crypto with real arkworks.

### 1.1 `Cargo.toml` — real dependencies
```toml
ark-groth16 = "0.4"
ark-bn254 = "0.4"
ark-ff = "0.4"
ark-ec = "0.4"
ark-serialize = "0.4"
ark-relations = "0.4"
ark-crypto-primitives = "0.4"  # Poseidon hash
ark-std = "0.4"
prometheus = "0.13"            # metrics
tonic = "0.14"
prost = "0.13"
```

### 1.2 Split into modules
- `src/circuit.rs` — `ComplianceCircuit` implementing `ConstraintSystem`:
  proves "transaction amount < threshold AND sender not in sanctions set" without revealing either
- `src/poseidon.rs` — real `ark_crypto_primitives::hash::poseidon` wrapper
- `src/merkle.rs` — real Merkle proof verification with SHA-256
- `src/lib.rs` — PyO3 module, re-exports, fix corrupted emoji (line 187)
- `src/main.rs` — tonic gRPC server from `trinity.proto`, `/metrics` endpoint

### 1.3 Real implementations
- `verify_compliance_proof` → real `Groth16::<Bn254>::verify`
- `generate_compliance_proof` → real `Groth16::<Bn254>::prove`
- `poseidon_hash` → real Poseidon
- `verify_merkle_proof` → real Merkle verification
- Remove `mock_verify_proof`, `mock_generate_proof`, placeholder types

### 1.4 Fix bugs
- Fix `.is_some()` on `Arc` in tests → use `assert!` on successful construction
- Remove dead `start_zk_grpc_service` from `lib.rs` (duplicates main.rs)
- Remove unused imports in `main.rs` (`tokio::net`, `tonic::transport::Server`)

### 1.5 Fix Dockerfile
- Use `rust:1.95` (NOT alpine — arkworks doesn't compile well with musl)
- Proper layered caching:
  ```dockerfile
  COPY Cargo.toml Cargo.lock ./
  RUN mkdir src && echo 'fn main() {}' > src/main.rs && cargo build --release || true
  COPY src/ ./src/
  RUN cargo build --release
  ```
- Align binary name: `Cargo.toml` `[[bin]] name = "trinity-zk"` matches Dockerfile `COPY trinity-zk`

### 1.6 Tests
- `tests/circuit.rs` — prove + verify roundtrip
- `tests/poseidon.rs` — hash determinism
- `tests/merkle.rs` — valid/invalid Merkle proofs
- Property tests with `proptest`

---

## Phase 2: Go Backend — Real Marble-style service (Go 1.27.0)

### 2.1 Fix DB schema mismatch (issue #5)
- Update `storeTransaction` to use `trinity.transactions` schema
- `INSERT ... ON CONFLICT (transaction_id)` (not `id` UUID)
- Qualify schema: `trinity.transactions`

### 2.2 Fix NER contract bug (issue #4)
- NER will return `suspicious: bool` + `sanctions_matches` on each entity (fixed in Phase 3)
- Update Go `NEREntity` struct:
  ```go
  type NEREntity struct {
      Type             string          `json:"type"`
      Text             string          `json:"text"`
      Suspicious       bool            `json:"suspicious"`
      SanctionsMatches []SanctionMatch `json:"sanctions_matches,omitempty"`
  }
  ```
- `isSuspicious` checks `entity.Suspicious` instead of string matching

### 2.3 Replace custom `contains` with `strings.Contains` (issue #10)
- Delete `contains` and `containsMiddle`
- Use `strings.Contains` from stdlib

### 2.4 Replace Firebase with Redis pub/sub
- Remove `firebase.google.com/go/v4` and `google.golang.org/api` from `go.mod`
- Add `github.com/redis/go-redis/v9`
- `TransactionService` holds a `*redis.Client` instead of `*firebase.App`
- After processing, publish transaction events to Redis channels:
  - `trinity:transactions` — all processed transactions
  - `trinity:flagged` — flagged transactions only
  - `trinity:sars` — SAR-generated events
- Other services/subscribers can listen to these channels for real-time updates
- Health check verifies Redis connection (Ping) instead of Firebase

### 2.5 Add structured logging
- Use `log/slog` (stdlib in Go 1.27)
- JSON format for production
- Request ID middleware

### 2.6 Add Prometheus metrics
- `github.com/prometheus/client_golang`
- `/metrics` endpoint
- Counters: `transactions_processed_total`, `transactions_flagged_total`, `sars_generated_total`
- Histograms: `transaction_processing_duration_seconds`

### 2.7 Add gRPC client for Rust ZK service
- Generate Go client from `trinity.proto`
- Call `verify_compliance_proof` after NER, before LLM, for suspicious transactions
- This completes the actual "Trinity" pipeline: Go → NER → ZK → LLM

### 2.8 Config validation
- `DATABASE_URL` required — fail fast at startup if missing
- `REDIS_URL` required — fail fast if missing
- `NER_ENDPOINT`, `LLM_ENDPOINT`, `ZK_ENDPOINT` with defaults
- `PORT` default 8080

### 2.9 Update `go.mod`
- `go 1.27.0`
- Remove Firebase deps (saves ~40 transitive deps)
- Add `github.com/redis/go-redis/v9`, `github.com/prometheus/client_golang`
- Update Dockerfile to `golang:1.27-alpine`

### 2.10 Tests
- `main_test.go` with `httptest` + mocked NER/LLM/ZK endpoints
- Use `miniredis` for Redis pub/sub tests
- Test `ProcessTransaction`, `isSuspicious`, `Health`

---

## Phase 3: Python NER Service — Real GLINER (Python 3.14)

### 3.1 Remove mock fallback
- Delete `_mock_entity_detection` and `GLINER_AVAILABLE` try/except
- Fail at startup if GLINER can't load:
  ```python
  raise RuntimeError("GLINER model required — install: pip install gliner")
  ```
- Move `gliner` from optional to required in `pyproject.toml`
- GLINER model downloads on first startup (HuggingFace cache)
- `GLINER_CACHE_DIR` env var controls cache location (defaults to `/app/models`)
- Docker volume `gliner_models` mounts to `GLINER_CACHE_DIR` — caches across restarts

### 3.2 Fix entity type mutation (issue #4)
- Stop mutating `entity.type` to `"Person* (SUSPICIOUS)"`
- Update `Entity` Pydantic model:
  ```python
  class Entity(BaseModel):
      type: str
      text: str
      suspicious: bool = False
      sanctions_matches: List[SanctionMatch] = []
  ```
- `_enhance_entities_with_sanctions` sets `entity.suspicious = True`

### 3.3 Add Redis caching
- `pyproject.toml` already lists `redis` — actually use it
- Cache GLINER predictions by text hash
- Cache sanctions lookups
- `config.py` reads `REDIS_URL` from env

### 3.4 Add Prometheus metrics
- `prometheus_client` dependency
- Counters: `ner_requests_total`, `ner_entities_detected_total`, `ner_suspicious_found_total`
- Histogram: `ner_inference_duration_seconds`
- `/metrics` endpoint

### 3.5 Type the `/analyze` endpoint (issue #15)
- Replace `transaction: Dict` with `TransactionAnalysisRequest` Pydantic model
- Add `TransactionAnalysisResponse` response model

### 3.6 Structured logging
- Use `structlog` for JSON logs

### 3.7 Config module
- `config.py` — pydantic-settings, reads all env vars
- `GLINER_MODEL`, `REDIS_URL`, `HOST`, `PORT`

### 3.8 Tests
- `tests/test_ner_service.py` with `httpx` + `pytest`
- Mock GLINER model via dependency injection (proper test double, not mock fallback)
- Test `/detect`, `/analyze`, `/health`

### 3.9 Update `pyproject.toml`
- `requires-python = ">=3.14,<3.15"`
- Move `gliner` to required deps
- Update classifiers

---

## Phase 4: LLM Service — Pluggable OpenAI / Ollama (Python 3.14)

### 4.1 Pluggable provider architecture
- `providers/base.py` — `LLMProvider` protocol:
  ```python
  class LLMProvider(Protocol):
      async def chat(self, messages: list[dict], json_mode: bool = False) -> str: ...
      async def health(self) -> bool: ...
      @property
      def name(self) -> str: ...
  ```
- `providers/openai.py` — OpenAI implementation (uses `openai` SDK)
- `providers/ollama.py` — Ollama implementation (uses `httpx` to call Ollama REST API at `OLLAMA_HOST`)
- Provider selected at startup via `LLM_PROVIDER` env var (`openai` or `ollama`)
- Default: `ollama` (fully self-hosted, no API key needed)
- Fail fast at startup if selected provider can't connect

### 4.2 Remove mock provider from production path
- Delete `_mock_llm_response` and `"mock"` provider
- No silent fallback — if provider fails, return HTTP 503 with clear error
- Test doubles via dependency injection in tests only (inject fake `LLMProvider`)

### 4.3 Fix default provider (issue #7)
- Validate at startup — crash with clear error if provider not configured
- If `LLM_PROVIDER=openai` but no `OPENAI_API_KEY` → clear error
- If `LLM_PROVIDER=ollama` but Ollama unreachable → clear error
- No silent 400 at request time

### 4.4 Fix fragile risk parsing (issue #14)
- Request structured JSON output from LLM:
  - OpenAI: `response_format={"type": "json_object"}`
  - Ollama: `format: "json"` in API call
- Parse `{"risk_level": "High", "requires_sar": true, ...}` directly
- Fallback: regex `Risk Level:\s*(\w+)` if JSON parse fails

### 4.5 Remove unused deps
- Remove `anthropic` and `cohere` from `pyproject.toml` (not used)
- Keep `openai` (for OpenAI provider)
- Add `httpx` (already transitively present, for Ollama calls)
- No new heavy deps — Ollama is a separate service, called via HTTP

### 4.6 Structured logging + Prometheus
- `structlog` for JSON logs
- `prometheus_client`:
  - `llm_requests_total` (label: `provider`)
  - `llm_investigations_total`, `llm_sars_generated_total`
  - `llm_request_duration_seconds` (label: `provider`)
- `/metrics` endpoint

### 4.7 Config module
- `config.py`:
  - `LLM_PROVIDER` — `openai` or `ollama` (default: `ollama`)
  - `OPENAI_API_KEY` — required only if provider is `openai`
  - `OPENAI_MODEL` — default `gpt-4`
  - `OLLAMA_HOST` — default `http://localhost:11434`
  - `OLLAMA_MODEL` — default `llama3.2`
  - `HOST`, `PORT`

### 4.8 Prompt templates
- `prompts.py` — extract investigation + SAR prompts into named templates
- Use Jinja2 (already in deps) for templating
- Prompts work with both OpenAI and Ollama (same prompt, different API)

### 4.9 Tests
- `tests/test_llm_service.py` with fake `LLMProvider` (dependency injection)
- Test `/chat`, `/investigate`, `/health`, `/providers` with both provider paths
- No real API calls in tests

### 4.10 Update `pyproject.toml`
- `requires-python = ">=3.14,<3.15"`
- Remove `anthropic`, `cohere`
- Keep `openai`, add `httpx` explicitly
- Update classifiers

---

## Phase 5: Demo Script — Real end-to-end

### 5.1 Fix 100 vs 1000 bug (issue #8)
- `range(100)` → `range(1000)` or `--count N` CLI arg
- `final_tps` uses actual count

### 5.2 Fix bare `except:` (issue #13)
- Replace with `except Exception:`

### 5.3 Add CLI args
- `--count N` — number of high-volume transactions
- `--scenario {sanctions,structuring,high-volume,all}` — run specific scenario
- `--check-only` — verify all services healthy, then exit

### 5.4 Fix port assumptions
- Read endpoints from env: `GO_BACKEND_URL`, `NER_URL`, `LLM_URL`, `ZK_URL`
- Defaults matching docker-compose

### 5.5 Show ZK step in demo output
- After Phase 2.6, Go calls ZK internally
- Demo output should show: Go → NER → ZK → LLM pipeline

### 5.6 Update `pyproject.toml`
- `requires-python = ">=3.14,<3.15"`

---

## Phase 6: Deployment & Infra

### 6.1 Fix `docker-compose.yml`
- Move to `deploy/docker-compose.yml`
- Fix all paths (`../services/go-backend` etc.)
- Create `deploy/monitoring/` with real configs
- Ports: Go 8080, LLM 8081, NER 9000, ZK 50053, Postgres 5433, Redis 6380, Ollama 11434
- Add `depends_on` ZK for Go backend
- `env_file: .env`
- Python images: `python:3.14-slim`
- Go image: `golang:1.27-alpine`
- Rust image: `rust:1.95` (not alpine)
- **Add Ollama service:**
  ```yaml
  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - ollama_models:/root/.ollama
    # GPU support optional via deploy.resources
  ```
- **Add GLINER volume** for NER model caching:
  ```yaml
  python-ner:
    volumes:
      - gliner_models:/app/models
  ```
- **Add volumes:**
  ```yaml
  volumes:
    postgres_data:
    prometheus_data:
    grafana_data:
    gliner_models:    # NEW — GLINER model cache
    ollama_models:    # NEW — Ollama model cache
  ```
- LLM service `depends_on: ollama` when `LLM_PROVIDER=ollama`

### 6.2 Prometheus config
- `deploy/monitoring/prometheus.yml`
- Scrape all 4 services' `/metrics` endpoints

### 6.3 Grafana dashboards
- `deploy/monitoring/grafana/dashboards/trinity-overview.json`
- Transaction throughput, flagging rate, SAR count, per-service latency
- `deploy/monitoring/grafana/datasources/prometheus.yml`

### 6.4 GitHub Actions CI
- `.github/workflows/ci.yml`:
  - **Rust:** `cargo fmt --check` + `cargo clippy` + `cargo test` + `cargo build --release`
  - **Go:** `golangci-lint` + `go test` + `go build`
  - **Python NER + LLM + demo:** `ruff check` + `mypy` + `pytest`
  - **Docker:** `docker compose config` validation
  - Python matrix: 3.14 (single version — it's the target)

### 6.5 Makefile targets
```makefile
make up          # docker compose up -d
make down        # docker compose down
make test        # run all tests across services
make lint        # lint all services
make demo        # run demo script
make build       # build all Docker images
make clean       # remove venvs, target/, build artifacts
make proto       # regenerate protobuf stubs
make ollama-pull # pull Ollama model (llama3.2) for local LLM
make setup       # first-time setup: cp .env.example .env + ollama-pull
```

### 6.6 `.env.example`
- Every env var with comments
- No real secrets

### 6.7 Update README
- New directory structure
- Quickstart: `make setup && make up && make demo`
- Document each service's endpoints
- Document architecture

### 6.8 Update `.gitignore`
- Add: `services/*/target/`, `services/*/.venv/`, `deploy/monitoring/grafana_data/`, `.env`

---

## Phase 7: Verification

### 7.1 Per-service
- **Rust:** `cargo fmt --check && cargo clippy -- -D warnings && cargo test && cargo build --release`
- **Go:** `go vet && go test ./... && go build`
- **Python NER:** `ruff check . && mypy . && pytest`
- **Python LLM:** `ruff check . && mypy . && pytest`
- **Demo:** `ruff check . && pytest`

### 7.2 Integration
- `docker compose up` — all services healthy
- `curl localhost:8080/api/v1/health` → healthy
- `curl localhost:9000/health` → healthy
- `curl localhost:8081/health` → healthy
- `curl localhost:50053/health` → OK
- `curl localhost:8080/metrics` → Prometheus metrics
- Run `make demo` end-to-end

### 7.3 CI
- Push to branch, verify all CI checks pass

---

## Execution Order

```
Phase 0 (contracts + scaffolding)
   ├─→ Phase 1 (Rust ZK)      ─┐
   ├─→ Phase 2 (Go backend)   ─┤
   ├─→ Phase 3 (Python NER)   ─┤  (parallel after Phase 0)
   └─→ Phase 4 (LLM service)  ─┘
              │
              ▼
        Phase 5 (demo script)
              │
              ▼
        Phase 6 (deploy + infra)
              │
              ▼
        Phase 7 (verification)
```

Phases 1-4 run in parallel via subagents after Phase 0 completes.

---

## Resolved Decisions

| Question | Decision | Rationale |
|----------|----------|-----------|
| LLM provider | Pluggable: OpenAI + Ollama (default: Ollama) | Fully self-hosted option. No API key required for local/demo. OpenAI available for production. |
| GLINER model in Docker | Runtime download + volume cache | Smaller image, cached across restarts via `gliner_models` volume. |
| Firebase | Replaced with Redis pub/sub | No Google Cloud dependency. Redis already in stack. Go publishes to `trinity:transactions`, `trinity:flagged`, `trinity:sars` channels. |

---

## Bug Tracker (from initial review)

| # | Severity | Component | Issue | Phase |
|---|----------|-----------|-------|-------|
| 1 | Critical | Rust ZK | Tests won't compile (`.is_some()` on `Arc`) | 1 |
| 2 | Critical | Rust ZK | Binary name mismatch (`zk-server` vs `trinity-zk`) | 1 |
| 3 | Critical | Rust ZK | Dockerfile COPY broken | 1 |
| 4 | Critical | NER→Go | Entity type mutation breaks Go's suspicious check | 2, 3 |
| 5 | Critical | Go backend | SQL doesn't match DB schema | 0, 2 |
| 6 | Critical | Docker | Missing `monitoring/` directory | 6 |
| 7 | High | LLM | Defaults to OpenAI when unavailable | 4 |
| 8 | High | Demo | Reports 1000 TPS but processes 100 | 5 |
| 9 | High | Multiple | Port 8080 conflict between Go and LLM | 6 |
| 10 | Medium | Go | Reinvents `strings.Contains` | 2 |
| 11 | Medium | Rust | Unused imports, dead code | 1 |
| 12 | Low | Rust | Corrupted emoji | 1 |
| 13 | Medium | Demo | Bare `except:` | 5 |
| 14 | Medium | LLM | Fragile risk parsing | 4 |
| 15 | Low | NER | Untyped `/analyze` endpoint | 3 |
