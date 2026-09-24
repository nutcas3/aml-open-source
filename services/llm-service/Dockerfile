# Single-stage build for LLM Service (pluggable OpenAI / Ollama)
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

# Copy pyproject.toml and README.md first (better layer caching)
COPY pyproject.toml README.md ./

# Copy source code (includes providers/ package, config, metrics, prompts)
COPY trinity_llm/ ./trinity_llm/

# Install dependencies with uv.
# pyproject.toml lists: fastapi, uvicorn, pydantic, aiohttp, python-multipart,
#   openai, httpx, prometheus-client, structlog, pydantic-settings,
#   requests, python-dotenv, jinja2, tiktoken
# No anthropic / cohere — removed (not used).
RUN uv pip install --system -e .

# Create non-root user
RUN groupadd -r trinity && useradd -r -g trinity trinity

# Create directories
RUN mkdir -p /app/logs /app/data

# Change ownership
RUN chown -R trinity:trinity /app

# Switch to non-root user
USER trinity

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Run the application
CMD ["uvicorn", "trinity_llm.llm_service:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "4"]
