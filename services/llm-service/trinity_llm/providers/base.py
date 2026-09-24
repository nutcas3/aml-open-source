"""
Base protocol and data classes for LLM providers.

Every provider (OpenAI, Ollama, test fakes) implements the ``LLMProvider``
protocol so the service can swap implementations via dependency injection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class ChatMessage:
    """A single chat message in the OpenAI-style role/content format."""

    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(slots=True)
class LLMResponse:
    """Normalised response returned by every provider."""

    content: str
    model: str
    provider: str


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol every LLM provider must satisfy."""

    @property
    def name(self) -> str:
        """Short provider identifier (e.g. ``"openai"``, ``"ollama"``)."""
        ...

    async def chat(
        self,
        messages: list[ChatMessage],
        json_mode: bool = False,
    ) -> LLMResponse:
        """Send a chat completion request and return the response.

        When ``json_mode`` is ``True`` the provider should request structured
        JSON output (OpenAI ``response_format`` / Ollama ``format="json"``).
        """
        ...

    async def health(self) -> bool:
        """Return ``True`` if the provider backend is reachable/healthy."""
        ...
