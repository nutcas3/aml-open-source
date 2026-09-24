# Multi-stage build for the Trinity Guard Go backend (Go 1.27).
FROM golang:1.27-alpine AS builder

# Build dependencies.
RUN apk add --no-cache git ca-certificates tzdata

WORKDIR /app

# Copy the module files and all Go sources first. We run `go mod tidy` in the
# builder so the build is self-contained even if go.sum was not regenerated
# locally (it reconciles go.mod/go.sum against the actual imports, including
# test-only dependencies).
COPY go.mod go.sum ./
COPY main.go zk_client.go main_test.go ./
RUN go mod tidy

# Build a static binary (tests are not compiled into the binary).
RUN CGO_ENABLED=0 GOOS=linux go build -a -installsuffix cgo -o trinity-backend .

# -----------------------------------------------------------------------------
# Final stage
# -----------------------------------------------------------------------------
FROM alpine:latest

# ca-certificates for outbound HTTPS (NER/LLM), curl for the health check.
RUN apk --no-cache add ca-certificates tzdata curl

# Non-root user.
RUN addgroup -g 1001 -S trinity && \
    adduser -u 1001 -S trinity -G trinity

WORKDIR /app

# Copy the built binary.
COPY --from=builder /app/trinity-backend .

# Log directory.
RUN mkdir -p logs && chown -R trinity:trinity /app

USER trinity

EXPOSE 8080

# Liveness/readiness probe against the health endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/api/v1/health || exit 1

CMD ["./trinity-backend"]
