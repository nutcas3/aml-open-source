# Trinity Guard — Python NER Service (GLINER)
FROM python:3.14-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

# Set working directory
WORKDIR /app

# Copy pyproject.toml and README.md
COPY pyproject.toml README.md ./

# Copy source code
COPY trinity_ner/ ./trinity_ner/

# Install dependencies with uv (gliner is a required dep — no extras needed)
RUN uv pip install --system -e .

# Create non-root user
RUN groupadd -r trinity && useradd -r -g trinity trinity

# Create directories — /app/models is the GLINER cache volume mount point
RUN mkdir -p /app/models /app/logs /app/data

# Change ownership
RUN chown -R trinity:trinity /app

# Switch to non-root user
USER trinity

# Environment configuration
ENV HOST=0.0.0.0 \
    PORT=9000 \
    GLINER_MODEL=urchade/gliner_base \
    GLINER_CACHE_DIR=/app/models \
    REDIS_URL=redis://redis:6379

# Volume mount point for GLINER model cache (cached across restarts)
VOLUME ["/app/models"]

# Expose port
EXPOSE 9000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:9000/health || exit 1

# Run the application
CMD ["uvicorn", "trinity_ner.ner_service:app", "--host", "0.0.0.0", "--port", "9000", "--workers", "4"]
