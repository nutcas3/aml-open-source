"""
Configuration for the Trinity LLM Service.

All settings are read from environment variables (12-factor config).
Defaults are tuned for a self-hosted Ollama setup so the service runs
out-of-the-box without an OpenAI API key.
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service configuration loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Server ----
    host: str = "0.0.0.0"
    port: int = 8080

    # ---- LLM provider selection ----
    llm_provider: Literal["openai", "ollama"] = "ollama"

    # ---- OpenAI ----
    openai_api_key: str = ""
    openai_model: str = "gpt-4"

    # ---- Ollama (local, self-hosted) ----
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"


settings = Settings()
