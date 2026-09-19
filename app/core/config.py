from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me"  # noqa: S105


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

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 100
    # Short timeouts: on the redirect path a slow Redis is worse than a missing one,
    # because we can always fall back to PostgreSQL.
    redis_socket_timeout_seconds: float = 0.25

    # Link cache
    cache_ttl_seconds: int = 3600
    cache_ttl_jitter: float = 0.1
    cache_negative_ttl_seconds: int = 30

    # Short codes
    short_code_length: int = 7
    short_code_max_attempts: int = 5

    # Authentication
    jwt_secret: SecretStr = SecretStr(DEV_JWT_SECRET)
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "url-shortener"
    access_token_ttl_seconds: int = 15 * 60
    refresh_token_ttl_seconds: int = 14 * 24 * 60 * 60

    @model_validator(mode="after")
    def _require_real_secret_in_prod(self) -> "Settings":
        secret = self.jwt_secret.get_secret_value()
        if self.environment == "prod" and (secret == DEV_JWT_SECRET or len(secret) < 32):
            raise ValueError("APP_JWT_SECRET must be set to a random value of 32+ characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
