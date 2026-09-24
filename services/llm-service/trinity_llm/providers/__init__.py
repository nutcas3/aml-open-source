"""
Pluggable LLM providers for the Trinity LLM Service.

Provider selection happens at startup via the ``LLM_PROVIDER`` env var
(``openai`` or ``ollama``). The :func:`get_provider` factory returns the
appropriate implementation and **fails fast** with a clear error if the
selected provider cannot be initialised.
"""

from __future__ import annotations

import structlog

from ..config import settings
from .base import ChatMessage, LLMProvider, LLMResponse
from .ollama import OllamaProvider
from .openai import OpenAIProvider

logger = structlog.get_logger(__name__)

__all__ = [
    "ChatMessage",
    "LLMProvider",
    "LLMResponse",
    "OllamaProvider",
    "OpenAIProvider",
    "get_provider",
]


def get_provider(provider: str | None = None) -> LLMProvider:
    """Factory that returns the configured LLM provider.

    Fails fast with a clear ``RuntimeError`` if the provider cannot be
    initialised (e.g. missing API key for OpenAI).

    For ``ollama`` the health check is deferred to startup — the server may
    come up after the service, so we only warn here.
    """

    selected = provider or settings.llm_provider

    if selected == "openai":
        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY required when LLM_PROVIDER=openai. "
                "Set it in your environment or .env file."
            )
        return OpenAIProvider()

    if selected == "ollama":
        return OllamaProvider()

    raise RuntimeError(
        f"Unknown LLM_PROVIDER '{selected}'. "
        "Supported values: 'openai', 'ollama'."
    )
