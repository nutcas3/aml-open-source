# Trinity Guard — Go Backend

The Go backend is the orchestration layer of the Trinity Guard AML pipeline. It
receives a transaction, runs it through the Python NER service, verifies a ZK
compliance proof against the Rust ZK service (for suspicious transactions),
asks the LLM service for a SAR narrative, persists the result to PostgreSQL,
and fans the event out to Redis pub/sub.

```
HTTP ─▶ NER (HTTP /detect) ─▶ [suspicious?] ─▶ ZK (gRPC VerifyComplianceProof)
                                                  │
                                                  ▼
                                          LLM (HTTP /chat) ─▶ PostgreSQL ─▶ Redis pub/sub
```

* **Language:** Go 1.27.0
* **Web framework:** Gin
* **DB:** PostgreSQL (schema `trinity`, see `deploy/scripts/init-db.sql`)
* **Pub/sub:** Redis (`github.com/redis/go-redis/v9`)
* **Metrics:** Prometheus (`/metrics`)
* **Logging:** `log/slog` (JSON to stdout)
* **ZK client:** gRPC to the Rust ZK service (`contracts/proto/trinity.proto`)

## API

| Method | Path                              | Description                                   |
|--------|-----------------------------------|-----------------------------------------------|
| POST   | `/api/v1/transactions/process`    | Run a transaction through the compliance pipeline. |
| GET    | `/api/v1/statistics`              | Real aggregate stats from `trinity.transactions`. |
| GET    | `/api/v1/health`                  | DB + Redis + ZK connectivity check.           |
| GET    | `/metrics`                        | Prometheus scrape endpoint.                   |

### Process a transaction

```bash
curl -s localhost:8080/api/v1/transactions/process \
  -H 'Content-Type: application/json' \
  -d '{
    "transaction": {
      "id": "tx-001",
      "amount": 25000,
      "currency": "USD",
      "description": "offshore wire transfer",
      "sender": "Alice",
      "receiver": "M. Emmanuel",
      "timestamp": "2026-01-01T00:00:00Z",
      "status": "pending",
      "category": "transfer"
    }
  }'
```

Response:

```json
{
  "processed": true,
  "flagged": true,
  "reason": "Suspicious entities detected, SAR generated",
  "sar_generated": true,
  "sar_narrative": "...",
  "entities": [
    {"type": "Person", "text": "M. Emmanuel", "suspicious": true,
     "sanctions_matches": [{"sanction_id": "sanction_001", "name": "M. Emmanuel",
     "similarity": 0.99, "risk_level": "HIGH", "match_type": "fuzzy"}]}
  ],
  "zk_verified": true,
  "processing_time_ms": 12.4
}
```

## Configuration (environment variables)

| Variable        | Required | Default                | Description                          |
|-----------------|:--------:|------------------------|--------------------------------------|
| `DATABASE_URL`  | yes      | —                      | PostgreSQL DSN (`postgres://...`).   |
| `REDIS_URL`     | yes      | —                      | Redis URL (`redis://host:port` or `host:port`). |
| `NER_ENDPOINT`  | no       | `http://localhost:9000` | Python NER service base URL.        |
| `LLM_ENDPOINT`  | no       | `http://localhost:8080` | LLM service base URL.               |
| `ZK_ENDPOINT`   | no       | `localhost:50051`      | Rust ZK gRPC service address.        |
| `PORT`          | no       | `8080`                 | HTTP listen port.                    |

`DATABASE_URL` and `REDIS_URL` are **required** — the service fails fast at
startup if either is missing. `ZK_ENDPOINT` is optional: if the ZK service is
unreachable, a warning is logged and transactions continue without ZK
verification (`zk_verified=false`).

## Redis pub/sub channels

| Channel                 | Payload                | When                            |
|-------------------------|------------------------|---------------------------------|
| `trinity:transactions`  | `TransactionEvent` JSON | Every processed transaction.   |
| `trinity:flagged`       | `TransactionEvent` JSON | Only flagged transactions.     |
| `trinity:sars`          | SAR event JSON         | Only when a SAR is generated.   |

Subscribe from any service:

```go
sub := rdb.Subscribe(ctx, "trinity:flagged")
msg, _ := sub.ReceiveMessage(ctx)
fmt.Println(msg.Payload)
```

## Prometheus metrics

| Metric                                    | Type      | Labels |
|-------------------------------------------|-----------|--------|
| `transactions_processed_total`            | counter   | `status` (`ok`, `ner_error`) |
| `transactions_flagged_total`              | counter   | —      |
| `sars_generated_total`                    | counter   | —      |
| `transaction_processing_duration_seconds` | histogram | —      |

## ZK gRPC client

`zk_client.go` is a **hand-written** gRPC client for the Rust ZK compliance
service. The protobuf contract lives in `contracts/proto/trinity.proto`. Because
we cannot run `protoc` in every build environment, the message types and the
gRPC method descriptors are implemented manually:

* Message structs (`VerifyProofRequest`, `VerifyProofResponse`, …) match the
  proto field numbers.
* A custom `grpc/encoding.Codec` (`trinityCodec`) marshals/unmarshals the
  protobuf wire format directly via
  `google.golang.org/protobuf/encoding/protowire`, using the standard `proto`
  content-subtype so the Rust (tonic) server accepts the requests.
* `ZKClient` wraps `grpc.ClientConn` and exposes
  `VerifyComplianceProof`, `GenerateComplianceProof`, `PoseidonHash`,
  `VerifyMerkleProof`, and `Health`.

If generated stubs become preferred, run:

```bash
protoc --go_out=. --go-grpc_out=. contracts/proto/trinity.proto
```

and replace `zk_client.go` with the generated client.

## Database

The service writes to `trinity.transactions` (schema-qualified). The table
definition, indexes, demo sanctions, and views are in
`deploy/scripts/init-db.sql`. The `id` column is `VARCHAR(255)` and is used as
the `ON CONFLICT` target (upsert).

## Running

### Local

```bash
export DATABASE_URL="postgres://trinity:trinity@localhost:5432/trinity?sslmode=disable"
export REDIS_URL="redis://localhost:6379"
export NER_ENDPOINT="http://localhost:9000"
export LLM_ENDPOINT="http://localhost:8080"
export ZK_ENDPOINT="localhost:50051"
go run .
```

### Docker

```bash
docker build -t trinity-guard/go-backend .
docker run -p 8080:8080 \
  -e DATABASE_URL=... -e REDIS_URL=... \
  -e NER_ENDPOINT=http://ner:9000 \
  -e LLM_ENDPOINT=http://llm:8080 \
  -e ZK_ENDPOINT=zk:50051 \
  trinity-guard/go-backend
```

The image uses a multi-stage build (`golang:1.27-alpine` → `alpine`), runs as a
non-root user, and ships a `HEALTHCHECK` against `/api/v1/health`.

## Tests

```bash
# Unit tests (NER/LLM via httptest, Redis via miniredis) — no DB needed:
go test ./...

# DB-dependent tests (ProcessTransaction end-to-end, Health) need a real
# PostgreSQL matching deploy/scripts/init-db.sql:
export DATABASE_URL="postgres://trinity:trinity@localhost:5432/trinity?sslmode=disable"
go test ./...
```

Tests cover:

* `isSuspicious` — pure logic over the NER `suspicious` contract.
* `callNERService` / `callLLMService` — httptest servers, including error paths.
* `publishEvents` — miniredis pub/sub on all three channels.
* `ProcessTransaction` — suspicious and non-suspicious flows end-to-end.
* `Health` — healthy and Redis-down paths.
* `trinityCodec` — protobuf wire round-trips for the ZK messages.

## Notes

* Firebase has been removed entirely. Real-time fan-out is handled by Redis
  pub/sub.
* The custom `contains`/`containsMiddle` helpers were replaced with
  `strings.Contains` from the standard library.
* Structured JSON logging is emitted to stdout via `log/slog`; every request
  carries an `X-Request-ID` (generated or forwarded) that is threaded through
  log entries.
