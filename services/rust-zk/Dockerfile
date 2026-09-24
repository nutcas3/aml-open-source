# Multi-stage build for the Trinity Guard Rust ZK Service.
#
# Build context MUST be the repository root so the shared proto contract is
# accessible. In docker-compose:
#   build:
#     context: ..
#     dockerfile: services/rust-zk/Dockerfile
#
# Uses the debian-based rust image (NOT alpine) because arkworks has known
# issues compiling against musl libc. The gRPC binary builds with default
# features (no Python linkage) so it links cleanly without a Python interpreter.
FROM rust:1.95 AS builder

# Build dependencies for tonic/prost (protoc) and the arkworks stack.
RUN apt-get update && apt-get install -y --no-install-recommends \
        protobuf-compiler \
        libssl-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Mirror the repository layout so build.rs's relative proto path
# (../../contracts/proto/trinity.proto) resolves identically to local builds.
WORKDIR /app/services/rust-zk

# --- Layered caching ---------------------------------------------------------
# Copy only the manifest + lockfile first and build a stub crate so dependency
# compilation is cached across source changes.
COPY services/rust-zk/Cargo.toml services/rust-zk/Cargo.lock ./

# The proto contract is needed by build.rs (it references
# ../../contracts/proto/trinity.proto from services/rust-zk/), so place it at
# the matching repo-relative location inside the image.
COPY contracts/proto/trinity.proto /app/contracts/proto/trinity.proto

# Create a stub source tree so cargo can resolve and fetch dependencies.
# Stub both the binary and the library so the heavy arkworks dependency tree
# is compiled and cached; the real source is copied in the next step.
RUN mkdir -p src/proto && \
    echo 'fn main() {}' > src/main.rs && \
    echo '' > src/lib.rs && \
    cargo build --release || true

# --- Real build --------------------------------------------------------------
COPY services/rust-zk/build.rs ./build.rs
COPY services/rust-zk/src/ ./src/

# Build the gRPC binary with default features (no Python linkage).
RUN cargo build --release

# --- Final runtime image -----------------------------------------------------
FROM debian:bookworm-slim

# Runtime deps: ca-certificates for gRPC TLS roots, curl for the healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user.
RUN groupadd -g 1001 trinity && \
    useradd -u 1001 -g trinity -s /bin/sh -m trinity

WORKDIR /app

# Copy the release binary from the builder stage.
COPY --from=builder /app/services/rust-zk/target/release/trinity-zk /app/trinity-zk

# Create runtime directories.
RUN mkdir -p logs data && chown -R trinity:trinity /app

# Switch to the non-root user.
USER trinity

# gRPC service port (matches docker-compose) and metrics port.
EXPOSE 50053 9100

# Health check against the HTTP /health endpoint on the metrics port.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:9100/health || exit 1

# Run the gRPC + metrics server.
CMD ["/app/trinity-zk"]
