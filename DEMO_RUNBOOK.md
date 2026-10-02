# Trinity Guard — Live Demo Runbook

Everything you need to run the stack locally and demo the full pipeline:
**Go → Python/GLINER → Rust/Groth16 → LLM**, with Postgres + Redis underneath.

No Docker image builds required — services run as local processes against
containerized Postgres + Redis.

---

## 0. Prerequisites

| Tool | Version | Check |
|---|---|---|
| Docker (OrbStack/Desktop) | any recent | `docker info` |
| Go | 1.27+ | `go version` |
| Rust | 1.95+ | `rustc --version` |
| uv + Python | 3.14 | `uv --version` |
| Ollama *(optional, for SAR step)* | any | `ollama --version` |

## 1. One-time setup

```bash
cd trinity-guard-pycon-kenya-2026

# Python envs
cd services/python-ner && uv sync --extra dev && cd ../..
cd services/llm-service && uv sync --extra dev && cd ../..
cd demo && uv sync --extra dev && cd ..

# Build local binaries
cd services/go-backend && go build -o trinity-backend . && cd ../..
cd services/rust-zk && cargo build --release && cd ../..
```

## 2. Start the stack (5 terminals)

**Terminal 1 — infrastructure:**
```bash
docker compose -f deploy/docker-compose.yml up -d postgres redis
```

**Terminal 2 — Rust ZK** (gRPC :50053, HTTP health/metrics :9100):
```bash
cd services/rust-zk && ./target/release/trinity-zk
```

**Terminal 3 — Python NER** (:9000 — first run downloads GLINER ~600MB, then cached):
```bash
cd services/python-ner
mkdir -p models-cache
GLINER_CACHE_DIR=./models-cache REDIS_URL=redis://localhost:6380 \
  uv run uvicorn trinity_ner.ner_service:app --host 0.0.0.0 --port 9000
```

**Terminal 4 — LLM service** (:8081 — runs degraded without Ollama, that's OK):
```bash
cd services/llm-service && uv run uvicorn trinity_llm.llm_service:app --host 0.0.0.0 --port 8081
```

**Terminal 5 — Go backend** (:8080):
```bash
cd services/go-backend
DATABASE_URL='postgres://marble_user:marble_password@localhost:5433/marble_trinity?sslmode=disable' \
REDIS_URL='redis://localhost:6380' \
NER_ENDPOINT='http://localhost:9000' \
ZK_ENDPOINT='localhost:50053' \
LLM_ENDPOINT='http://localhost:8081' \
PORT=8080 ./trinity-backend
```

Optional — for the SAR narrative step:
```bash
ollama serve && ollama pull llama3.2
```

## 3. Health checks

```bash
curl -s http://localhost:8080/api/v1/health | python3 -m json.tool   # Go (db+redis+zk)
curl -s http://localhost:9000/health | python3 -m json.tool          # NER (gliner+redis)
curl -s http://localhost:9100/health | python3 -m json.tool          # Rust ZK
curl -s http://localhost:8081/health | python3 -m json.tool          # LLM (degraded = OK)
```

## 4. Run the demo

```bash
cd demo
uv run python -m trinity_demo.live_demo --check-only            # health check only
uv run python -m trinity_demo.live_demo --scenario sanctions    # sanctioned entity
uv run python -m trinity_demo.live_demo --scenario structuring  # sub-$10k pattern
uv run python -m trinity_demo.live_demo --scenario high-volume --count 500
uv run python -m trinity_demo.live_demo                          # all scenarios
```

## 5. Curl samples (the talking points)

The API contract wraps the transaction: `{"transaction": {...}}`.
Timestamps must be RFC3339 **with timezone** (`Z` or `+00:00`).

### a) Clean M-Pesa transfer — passes everything

```bash
curl -s -X POST http://localhost:8080/api/v1/transactions/process \
  -H 'Content-Type: application/json' \
  -d '{"transaction":{"id":"mpesa-001","amount":4500,"currency":"KES",
       "sender":"M-Pesa +254712345678","receiver":"Naivas Supermarket",
       "description":"POS payment","timestamp":"2026-10-01T12:00:00Z"}}' \
  | python3 -m json.tool
```
→ `"flagged": false`, `"reason": "Transaction passed all compliance checks"`

### b) Sanctioned sender — flagged + real Groth16 proof verified

```bash
curl -s -X POST http://localhost:8080/api/v1/transactions/process \
  -H 'Content-Type: application/json' \
  -d '{"transaction":{"id":"mpesa-002","amount":8500,"currency":"USD",
       "sender":"Robert Mugabe","receiver":"Offshore Holdings Ltd",
       "description":"Wire transfer","timestamp":"2026-10-01T12:05:00Z"}}' \
  | python3 -m json.tool
```
→ `"flagged": true`, `sanctions_matches` with `risk_level: HIGH`,
`"zk_verified": true` — the amount stayed private; only the proof crossed the wire.

### c) M-Pesa number → Poseidon hash (the privacy talking point)

ZK only engages on *flagged* transactions. Here a raw M-Pesa MSISDN is the
sender and `Moneycorp` (sanctioned) is the receiver — the phone number is
Poseidon-hashed by Go before the ZK service ever sees it, then the circuit
proves `amount < $10k AND sender_hash != sanctions_root`.

```bash
curl -s -X POST http://localhost:8080/api/v1/transactions/process \
  -H 'Content-Type: application/json' \
  -d '{"transaction":{"id":"mpesa-003","amount":9200,"currency":"KES",
       "sender":"+254700111222","receiver":"Moneycorp",
       "description":"Bank transfer","timestamp":"2026-10-01T12:10:00Z"}}' \
  | python3 -m json.tool
```
→ `"flagged": true`, `Moneycorp` direct sanctions match, `"zk_verified": true` —
the MSISDN never left the Go process as plaintext; only its Poseidon hash did.

```bash
# Watch the hash counter tick (one per sender + one for the sanctions root)
curl -s http://localhost:9100/metrics | grep poseidon_hashes_total
```

If you want to see the actual digest (`brew install grpcurl` first):
```bash
grpcurl -plaintext -d '{"data":"KzI1NDcwMDExMTIyMg=="}' \
  localhost:50053 trinity.ZKComplianceService/PoseidonHash
# → {"hash": "<32-byte LE field element, base64>"}  — that's what the verifier sees
```
(`data` is base64 of `+254700111222`.)

### d) Over-threshold — proof cryptographically rejected

```bash
curl -s -X POST http://localhost:8080/api/v1/transactions/process \
  -H 'Content-Type: application/json' \
  -d '{"transaction":{"id":"mpesa-004","amount":25000,"currency":"USD",
       "sender":"Robert Mugabe","receiver":"Shell Corp",
       "description":"Bulk transfer","timestamp":"2026-10-01T12:15:00Z"}}' \
  | python3 -m json.tool
```
→ `"zk_verified": false` — no valid proof exists for `amount > threshold`.
Still flagged via NER — the layers are independent.

### e) Direct NER call — show GLINER raw output

```bash
curl -s -X POST http://localhost:9000/detect \
  -H 'Content-Type: application/json' \
  -d '{"text":"Wire transfer Robert Mugabe Offshore Holdings Ltd"}' \
  | python3 -m json.tool
```

### f) Statistics — Postgres persistence proof

```bash
curl -s http://localhost:8080/api/v1/statistics | python3 -m json.tool
```

### g) Metrics endpoints (for the observability slide)

```bash
curl -s http://localhost:8080/metrics | grep transactions_processed
curl -s http://localhost:9100/metrics | grep trinity_zk
```

## 6. Redis pub/sub — show the events flowing

```bash
docker exec deploy-redis-1 redis-cli SUBSCRIBE trinity:flagged
# then fire sample (b) in another terminal — the flag event prints live
```

## 7. Shutdown

```bash
# Ctrl-C the four service terminals, then:
docker compose -f deploy/docker-compose.yml down
```

## What the audience is actually seeing

| Step | What crosses the wire | What stays private |
|---|---|---|
| NER | transaction text | — |
| ZK generate | `amount‖sender_hash` (32B LE field elements) | threshold check happens in-circuit |
| ZK verify | proof bytes + public inputs | amount, sender identity |
| LLM | flagged tx + entities | skipped if Ollama down (503, still flagged) |
| Store/publish | outcome record | — |

**Honest caveat for Q&A:** the demo circuit proves `amount < threshold AND
sender_hash != sanctions_root`. Full sanctions non-membership would need a
Merkle-inclusion circuit — the `VerifyMerkleProof` RPC exists for audit trails
but isn't wired into the amount circuit yet.
