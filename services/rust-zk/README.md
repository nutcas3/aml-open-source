# Trinity Guard — Rust ZK Service ("The Shield")

High-performance Zero-Knowledge cryptography for the Trinity Guard AML pipeline.
This service provides real **arkworks Groth16** ZK-SNARK proofs over the **Bn254**
pairing curve, **Poseidon** hashing, and **SHA-256 Merkle** proof verification
for the privacy-preserving audit trail.

> No mocks. Every cryptographic operation uses a real, audited implementation.

---

## What it proves

The `ComplianceCircuit` is a Groth16 circuit that proves, **without revealing
the private data**:

> *"I know a transaction `amount` that is below a public `threshold`, and the
> Poseidon hash of the sender is not equal to the public sanctions root."*

The verifier learns **only** that the transaction is compliant — never the
amount or the sender identity.

### Constraint structure

- **Range check** — `amount < threshold` via 64-bit decomposition of
  `threshold - amount`. If `amount > threshold` the difference is a huge field
  element that cannot fit in 64 bits, so the constraint is unsatisfiable and
  proof generation fails.
- **Non-equality** — `sender_hash != sanctions_root` by proving
  `(sender_hash - sanctions_root)` has a multiplicative inverse. If the sender
  is sanctioned the inverse does not exist and proof generation fails.

---

## Module layout

| File | Responsibility |
|------|----------------|
| `src/circuit.rs` | `ComplianceCircuit` (`ConstraintSynthesizer`), public/private input types, Poseidon config. |
| `src/poseidon.rs` | Real Poseidon sponge hash (`ark_crypto_primitives::sponge::poseidon`) over Bn254. |
| `src/merkle.rs` | SHA-256 Merkle proof verification (RFC 6962 domain separation, constant-time root compare). |
| `src/lib.rs` | Core `ZKComplianceVerifier` (real Groth16 setup/prove/verify) + optional PyO3 bindings. |
| `src/main.rs` | `trinity-zk` gRPC server (`ZKComplianceService`) + Prometheus `/metrics` + HTTP `/health`. |
| `build.rs` | Compiles `contracts/proto/trinity.proto` into Rust gRPC stubs via `tonic-build`. |
| `tests/` | Integration tests: prove/verify roundtrip, Poseidon determinism, Merkle valid/invalid. |

---

## Two build targets

The crate produces **two** artifacts, controlled by Cargo features:

### 1. The gRPC binary (default)

```bash
cargo build --release
```

Builds the `trinity-zk` binary with **no Python linkage** (default features are
empty). This links cleanly without a Python interpreter and is what the
Dockerfile produces.

### 2. The Python extension module

```bash
cargo build --release --features python --lib
# or, recommended:
maturin develop --release --features python
```

Enables the `python` feature, which compiles the PyO3 `cdylib` (`trinity_zk`)
exposing `ZKComplianceVerifier`, `poseidon_hash`, `verify_merkle_proof`, and
`benchmark_zk_operations` to Python.

> The `python` feature gates PyO3 behind `#[cfg(feature = "python")]` so that
> `cargo build` (the gRPC binary) never pulls in libpython and always links.

---

## gRPC API

Defined in [`contracts/proto/trinity.proto`](../../contracts/proto/trinity.proto),
package `trinity`:

| RPC | Request | Response | Description |
|-----|---------|----------|-------------|
| `VerifyComplianceProof` | `proof_bytes, public_inputs` | `is_valid, error` | Real `Groth16::verify`. |
| `GenerateComplianceProof` | `private_data, public_data` | `proof_bytes, error` | Real `Groth16::prove`. |
| `PoseidonHash` | `data` | `hash, error` | Real Poseidon sponge. |
| `VerifyMerkleProof` | `leaf, proof[], root` | `is_valid, error` | SHA-256 Merkle verification. |
| `Health` | `{}` | `status, version, circuit_loaded` | Service health. |

### Ports

- **`50053`** — gRPC service (`ZKComplianceService`).
- **`9100`** — HTTP `/metrics` (Prometheus) and `/health` (Docker/K8s probes).

---

## Prometheus metrics

| Metric | Type | Description |
|--------|------|-------------|
| `trinity_zk_proofs_generated_total` | counter | Groth16 proofs generated. |
| `trinity_zk_proofs_verified_total` | counter | Groth16 proofs verified. |
| `trinity_zk_proof_verify_failures_total` | counter | Verifications that failed (invalid proof). |
| `trinity_zk_merkle_verifications_total` | counter | Merkle proof verifications. |
| `trinity_zk_poseidon_hashes_total` | counter | Poseidon hash computations. |
| `trinity_zk_grpc_requests_total{method}` | counter | gRPC requests by method. |

---

## Running locally

```bash
# gRPC + metrics server
RUST_LOG=info cargo run --release

# In another terminal, quick health check:
curl http://localhost:9100/health
curl http://localhost:9100/metrics
```

---

## Tests

```bash
cargo test
```

- `tests/circuit.rs` — full Groth16 prove → verify roundtrip; rejects
  over-threshold amounts and sanctioned senders.
- `tests/poseidon.rs` — Poseidon determinism, output size, input distinction.
- `tests/merkle.rs` — valid/invalid/tampered Merkle proofs.
- In-module tests in `lib.rs` — verifier construction, end-to-end roundtrip.

> The trusted-setup tests take ~1–3 seconds (real Groth16 key generation over
> Bn254). This is expected.

---

## Docker

The Dockerfile uses `rust:1.95` (debian-based, **not alpine** — arkworks has
known issues with musl libc) with layered dependency caching. The build context
must be the repository root so the shared proto contract is accessible:

```yaml
# in deploy/docker-compose.yml
rust-zk:
  build:
    context: ..
    dockerfile: services/rust-zk/Dockerfile
```

The binary is built with default features (no Python), runs as a non-root user,
and exposes `50053` (gRPC) and `9100` (metrics/health).

---

## Dependencies

Real arkworks crates (no mock crypto):

- `ark-groth16`, `ark-bn254`, `ark-ff`, `ark-ec`, `ark-serialize`,
  `ark-relations`, `ark-crypto-primitives` (sponge/Poseidon), `ark-snark`,
  `ark-std`
- `tonic` + `prost` (gRPC), `axum` (metrics/health HTTP), `prometheus` (metrics)
- `sha2` (Merkle), `blake3`, `pyo3` (optional, Python bindings)
- `tokio`, `serde`, `serde_json`, `anyhow`, `thiserror`, `tracing`

---

## License

MIT
