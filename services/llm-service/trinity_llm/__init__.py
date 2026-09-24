"""Trinity LLM Service — pluggable OpenAI / Ollama compliance automation."""

__version__ = "2.0.0"
__author__ = "Maurice Nyanja"

from .llm_service import app

__all__ = ["app", "__version__"]
