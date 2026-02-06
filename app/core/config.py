"""Application settings loaded from environment / .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://minirag:changeme@postgres:5432/minirag"

    # ── Redis ─────────────────────────────────────────────
    redis_url: str = "redis://redis:6379/0"

    # ── Qdrant ────────────────────────────────────────────
    qdrant_url: str = "http://qdrant:6333"

    # ── Security ──────────────────────────────────────────
    encryption_key: str = ""  # Fernet key – MUST be set in production
    jwt_secret_key: str = ""  # MUST be set in production
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # ── LLM ───────────────────────────────────────────────
    default_llm_model: str = "gpt-4o-mini"
    default_embedding_model: str = "text-embedding-3-small"


@lru_cache
def get_settings() -> Settings:
    return Settings()
