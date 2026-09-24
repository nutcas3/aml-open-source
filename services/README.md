# Trinity Guard Services

The four services that make up the Trinity Guard AML compliance system.

## Services

| Service | Directory | Language | Role |
|---------|-----------|----------|------|
| Go Backend | [`go-backend/`](./go-backend/) | Go 1.27 | The Muscle — transaction processing orchestrator |
| Python NER | [`python-ner/`](./python-ner/) | Python 3.14 | The Brain — GLINER entity resolution |
| Rust ZK | [`rust-zk/`](./rust-zk/) | Rust 1.95 | The Shield — arkworks Groth16 ZK-SNARKs |
| LLM Service | [`llm-service/`](./llm-service/) | Python 3.14 | AI Investigator — OpenAI/Ollama SAR generation |

## Shared Contracts

- [OpenAPI (REST)](../contracts/openapi.yaml) — HTTP schemas for all services
- [Protobuf (gRPC)](../contracts/proto/trinity.proto) — gRPC definitions for Go ↔ Rust ZK

## Running

See the [root README](../README.md) for deployment instructions.

```bash
# From the project root:
make setup    # First-time setup
make up       # Start all services
make demo     # Run the live demo
```
