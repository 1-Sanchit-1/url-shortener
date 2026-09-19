from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment variables prefixed with ``APP_``."""

    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    environment: Literal["dev", "test", "prod"] = "dev"
    app_name: str = "url-shortener"
    base_url: str = "http://localhost:8000"
    log_level: str = "INFO"

    # PostgreSQL
    database_url: str = "postgresql+asyncpg://shortener:shortener@localhost:5432/shortener"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout_seconds: float = 5.0

    # Short codes
    short_code_length: int = 7
    short_code_max_attempts: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
