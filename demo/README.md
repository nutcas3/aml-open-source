# Trinity Guard Demo

Live demonstration script for the Trinity Guard AML compliance system.

## Usage

```bash
# Run all scenarios (requires services to be running)
python -m trinity_demo.live_demo

# Check service health only
python -m trinity_demo.live_demo --check-only

# Run specific scenario
python -m trinity_demo.live_demo --scenario sanctions
python -m trinity_demo.live_demo --scenario structuring
python -m trinity_demo.live_demo --scenario high-volume

# Custom transaction count for high-volume scenario
python -m trinity_demo.live_demo --scenario high-volume --count 5000
```

## Scenarios

1. **Sanctions Evasion** — Detects a sanctioned individual via GLINER NER
2. **Structuring Detection** — Identifies smurfing pattern (sub-$10K transactions)
3. **High-Volume Processing** — Processes N transactions in parallel batches

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GO_BACKEND_URL` | `http://localhost:8080` | Go backend URL |
| `NER_URL` | `http://localhost:9000` | Python NER URL |
| `LLM_URL` | `http://localhost:8081` | LLM service URL |
| `ZK_URL` | `http://localhost:9100` | Rust ZK metrics/health URL |

## Running with Docker

```bash
# From project root:
make up
make demo
```
