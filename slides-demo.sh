#!/bin/bash

# Trinity Guard — slides-tape demo helper
# Health-checks all services, then runs the live demo against the real stack.

set -u

echo "=== Trinity Guard Live Demo ==="
echo "nutcas3 - Maurice Nyanja"
echo ""

# Service endpoints (external docker-compose ports)
GO_URL="${GO_BACKEND_URL:-http://localhost:8080}"
NER_URL="${NER_URL:-http://localhost:9000}"
LLM_URL="${LLM_URL:-http://localhost:8081}"
ZK_URL="${ZK_URL:-http://localhost:9100}"

check() {
    local name="$1" url="$2"
    if curl -sf --max-time 3 "$url" > /dev/null 2>&1; then
        echo "   $name: RUNNING"
    else
        echo "   $name: DOWN  ($url)"
        return 1
    fi
}

echo "1. Service Health"
echo ""
down=0
check "Go backend   (:8080)" "$GO_URL/api/v1/health" || down=1
check "Python NER   (:9000)" "$NER_URL/health"       || down=1
check "Rust ZK      (:9100)" "$ZK_URL/health"        || down=1
check "LLM service  (:8081)" "$LLM_URL/health"       || down=1
echo ""

if [ "$down" -ne 0 ]; then
    echo "   Some services are down. Start the stack with: make up"
    exit 1
fi

echo "2. Running Trinity pipeline demo..."
echo ""
cd "$(dirname "$0")/demo"
uv run python -m trinity_demo.live_demo "$@"

echo ""
echo "3. Metrics endpoints"
echo "   Prometheus: http://localhost:9090"
echo "   Grafana:    http://localhost:3000"
echo ""
echo "=== Demo Complete ==="
