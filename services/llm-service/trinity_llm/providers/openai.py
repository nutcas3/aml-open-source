"""
OpenAI LLM provider.

Uses the official ``openai`` SDK's async client (``openai.AsyncOpenAI``).
Configured via ``OPENAI_API_KEY`` and ``OPENAI_MODEL`` environment variables.
"""

from __future__ import annotations

from typing import Any

import structlog

from ..config import settings
from .base import ChatMessage, LLMProvider, LLMResponse

logger = structlog.get_logger(__name__)


class OpenAIProvider:
    """LLM provider backed by the OpenAI Chat Completions API."""

    name = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        # Import lazily so the module can be imported even if the SDK is
        # absent (e.g. in environments that only use Ollama).
        from openai import AsyncOpenAI

        self._api_key = api_key if api_key is not None else settings.openai_api_key
        self._model = model if model is not None else settings.openai_model

        if not self._api_key:
            raise RuntimeError(
                "OPENAI_API_KEY required when LLM_PROVIDER=openai. "
                "Set it in your environment or .env file."
            )

        self._client: AsyncOpenAI = AsyncOpenAI(api_key=self._api_key)
        logger.info(
            "openai_provider_initialised",
            model=self._model,
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        json_mode: bool = False,
    ) -> LLMResponse:
        """Call OpenAI chat completions, optionally in JSON mode."""

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": [m.to_dict() for m in messages],
            "temperature": 0.1,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        completion = await self._client.chat.completions.create(**kwargs)
        content = completion.choices[0].message.content or ""
        return LLMResponse(
            content=content,
            model=completion.model or self._model,
            provider=self.name,
        )

    async def health(self) -> bool:
        """Check whether the OpenAI client is initialised with an API key."""

        return bool(self._client and self._api_key)
