"""
Ollama LLM provider.

Calls the Ollama REST API (``/api/chat``) over HTTP using ``httpx``.
Configured via ``OLLAMA_HOST`` and ``OLLAMA_MODEL`` environment variables.
Ollama runs fully self-hosted — no API key required.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from ..config import settings
from .base import ChatMessage, LLMProvider, LLMResponse

logger = structlog.get_logger(__name__)


class OllamaProvider:
    """LLM provider backed by a local Ollama server."""

    name = "ollama"

    def __init__(
        self,
        host: str | None = None,
        model: str | None = None,
    ) -> None:
        self._host = host if host is not None else settings.ollama_host
        self._model = model if model is not None else settings.ollama_model
        self._client = httpx.AsyncClient(base_url=self._host, timeout=120.0)
        logger.info(
            "ollama_provider_initialised",
            host=self._host,
            model=self._model,
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        json_mode: bool = False,
    ) -> LLMResponse:
        """POST to ``/api/chat`` and return the assistant message."""

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [m.to_dict() for m in messages],
            "stream": False,
            "options": {"temperature": 0.1},
        }
        if json_mode:
            payload["format"] = "json"

        resp = await self._client.post("/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("message", {}).get("content", "")
        return LLMResponse(
            content=content,
            model=data.get("model", self._model),
            provider=self.name,
        )

    async def health(self) -> bool:
        """Check whether the Ollama server is reachable (``GET /api/tags``)."""

        try:
            resp = await self._client.get("/api/tags")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        """Close the underlying HTTP client."""

        await self._client.aclose()
