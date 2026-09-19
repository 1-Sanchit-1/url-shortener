from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.ratelimit.policy import RateLimitPolicy

DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me"  # noqa: S105


class Settings(BaseSettings):
    """Application settings, loaded from environment variables prefixed with ``APP_``."""

    model_config = SettingsConfigDict(
        env_prefix="APP_", env_file=".env", env_nested_delimiter="__", extra="ignore"
    )

    environment: Literal["dev", "test", "prod"] = "dev"
    app_name: str = "url-shortener"
    base_url: str = "http://localhost:8000"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    # Fraction of fast, successful requests that get an access log line. Errors
    # (>= 400) and slow requests are always logged; metrics count every request.
    access_log_sample_rate: float = 1.0
    access_log_slow_ms: float = 250.0

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
    # In-process L1 tier. The TTL bounds cross-process staleness if a pub/sub
    # invalidation is missed; the grace period is how long an expired entry may be
    # served while Redis and PostgreSQL are both unavailable.
    l1_cache_max_entries: int = 10_000
    l1_cache_ttl_seconds: float = 5.0
    l1_stale_grace_seconds: float = 300.0

    # Click analytics pipeline
    click_stream_name: str = "clicks"
    # Bounds Redis memory if consumers fall behind. Size it to hold several hours
    # of peak traffic, and alert on consumer lag well before it is reached.
    click_stream_maxlen: int = 1_000_000
    click_consumer_group: str = "click-ingest"
    click_batch_size: int = 500
    click_block_ms: int = 1000
    click_claim_idle_ms: int = 30_000
    # API-side batching of click events (see ClickPublisher).
    click_flush_interval_ms: int = 50
    click_flush_max_batch: int = 500
    click_buffer_capacity: int = 50_000
    worker_metrics_port: int = 9100
    analytics_salt: SecretStr = SecretStr("dev-only-analytics-salt")

    # Short codes
    short_code_length: int = 7
    short_code_max_attempts: int = 5

    # Authentication
    jwt_secret: SecretStr = SecretStr(DEV_JWT_SECRET)
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "url-shortener"
    access_token_ttl_seconds: int = 15 * 60
    refresh_token_ttl_seconds: int = 14 * 24 * 60 * 60

    # Rate limiting (token buckets). Override per field, e.g.
    # APP_RATE_LIMIT_AUTH__CAPACITY=20
    rate_limit_enabled: bool = True
    # Anonymous redirects, per client IP. Generous: this only stops scrapers and floods.
    rate_limit_redirect: RateLimitPolicy = RateLimitPolicy(capacity=200, refill_per_second=100)
    # Register/login/refresh, per client IP: 10 attempts, then one every 6 seconds.
    rate_limit_auth: RateLimitPolicy = RateLimitPolicy(capacity=10, refill_per_second=1 / 6)
    # Mutations, per user: a burst of 30, then 1 every 2 seconds.
    rate_limit_write: RateLimitPolicy = RateLimitPolicy(capacity=30, refill_per_second=0.5)
    # Authenticated reads, per user.
    rate_limit_read: RateLimitPolicy = RateLimitPolicy(capacity=120, refill_per_second=2)
    # Admins get proportionally larger buckets.
    rate_limit_admin_multiplier: float = 10.0

    @model_validator(mode="after")
    def _require_real_secret_in_prod(self) -> "Settings":
        secret = self.jwt_secret.get_secret_value()
        if self.environment == "prod" and (secret == DEV_JWT_SECRET or len(secret) < 32):
            raise ValueError("APP_JWT_SECRET must be set to a random value of 32+ characters")
        if self.environment == "prod" and self.analytics_salt.get_secret_value().startswith("dev-"):
            raise ValueError("APP_ANALYTICS_SALT must be set in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
