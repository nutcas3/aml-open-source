"""
Trinity Guard NER Service — Configuration.

All configuration is loaded from environment variables (12-factor app).
Uses pydantic-settings for validation and type coercion.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Service configuration loaded from environment / .env file."""

    # Server
    host: str = "0.0.0.0"
    port: int = 9000

    # GLINER model
    gliner_model: str = "urchade/gliner_base"
    gliner_cache_dir: str = "/app/models"
    gliner_labels: str = "Person,Company,Country,Illegal Activity"

    # Redis (caching + sanctions lookups)
    redis_url: str = "redis://localhost:6379"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
