import asyncio
from collections.abc import AsyncIterator

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.container import create_redis
from app.main import create_app
from app.ratelimit.policy import RateLimitPolicy
from app.ratelimit.token_bucket import TokenBucketLimiter
from tests.utils import DEFAULT_PASSWORD, register_and_login


@pytest.fixture
async def redis(settings: Settings) -> AsyncIterator[Redis]:
    client = create_redis(settings)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


async def test_bucket_allows_burst_then_denies(redis: Redis) -> None:
    limiter = TokenBucketLimiter(redis)
    policy = RateLimitPolicy(capacity=3, refill_per_second=1)

    decisions = [await limiter.acquire("k", policy) for _ in range(4)]
    assert [d.allowed for d in decisions] == [True, True, True, False]
    assert [d.remaining for d in decisions[:3]] == [2, 1, 0]
    assert decisions[3].retry_after_seconds == 1


async def test_bucket_refills_over_time(redis: Redis) -> None:
    limiter = TokenBucketLimiter(redis)
    policy = RateLimitPolicy(capacity=1, refill_per_second=20)

    assert (await limiter.acquire("k", policy)).allowed
    assert not (await limiter.acquire("k", policy)).allowed
    await asyncio.sleep(0.1)  # 20/s -> a full token after 50ms
    assert (await limiter.acquire("k", policy)).allowed


async def test_bucket_is_atomic_under_concurrency(redis: Redis) -> None:
    limiter = TokenBucketLimiter(redis)
    policy = RateLimitPolicy(capacity=10, refill_per_second=0.001)

    decisions = await asyncio.gather(*(limiter.acquire("k", policy) for _ in range(50)))
    assert sum(d.allowed for d in decisions) == 10


async def test_keys_are_isolated(redis: Redis) -> None:
    limiter = TokenBucketLimiter(redis)
    policy = RateLimitPolicy(capacity=1, refill_per_second=0.001)
    assert (await limiter.acquire("a", policy)).allowed
    assert (await limiter.acquire("b", policy)).allowed


async def test_idle_buckets_expire(redis: Redis) -> None:
    limiter = TokenBucketLimiter(redis)
    await limiter.acquire("k", RateLimitPolicy(capacity=10, refill_per_second=5))
    ttl_ms = await redis.pttl("rl:k")
    assert 0 < ttl_ms <= 3000


async def test_fails_open_when_redis_is_down(settings: Settings) -> None:
    broken = create_redis(settings.model_copy(update={"redis_url": "redis://127.0.0.1:1/0"}))
    try:
        decision = await TokenBucketLimiter(broken).acquire(
            "k", RateLimitPolicy(capacity=1, refill_per_second=1)
        )
        assert decision.allowed
    finally:
        await broken.aclose()


@pytest.fixture
async def strict_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    strict = settings.model_copy(
        update={
            "rate_limit_auth": RateLimitPolicy(capacity=2, refill_per_second=0.01),
            "rate_limit_write": RateLimitPolicy(capacity=2, refill_per_second=0.01),
            "rate_limit_admin_multiplier": 3.0,
        }
    )
    app = create_app(strict)
    transport = ASGITransport(app=app)
    async with (
        LifespanManager(app),
        AsyncClient(transport=transport, base_url="http://test") as http,
    ):
        yield http


async def test_login_is_limited_per_ip(client: AsyncClient, strict_client: AsyncClient) -> None:
    await register_and_login(client, "ivy@example.com")
    creds = {"email": "ivy@example.com", "password": "wrong-password!"}

    statuses = [
        (await strict_client.post("/api/v1/auth/login", json=creds)).status_code for _ in range(3)
    ]
    assert statuses == [401, 401, 429]

    blocked = await strict_client.post(
        "/api/v1/auth/login", json={"email": "ivy@example.com", "password": DEFAULT_PASSWORD}
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0
    assert blocked.json()["error"]["code"] == "rate_limited"


async def test_writes_are_limited_per_user_with_headers(
    client: AsyncClient, strict_client: AsyncClient
) -> None:
    alice = await register_and_login(client, "alice@example.com")
    bob = await register_and_login(client, "bob@example.com")
    body = {"target_url": "https://example.com"}

    first = await strict_client.post("/api/v1/urls", json=body, headers=alice)
    assert first.headers["x-ratelimit-limit"] == "2"
    assert first.headers["x-ratelimit-remaining"] == "1"
    await strict_client.post("/api/v1/urls", json=body, headers=alice)
    assert (await strict_client.post("/api/v1/urls", json=body, headers=alice)).status_code == 429

    # Bob has his own bucket.
    assert (await strict_client.post("/api/v1/urls", json=body, headers=bob)).status_code == 201


async def test_admins_get_larger_buckets(
    admin_headers: dict[str, str], strict_client: AsyncClient
) -> None:
    response = await strict_client.post(
        "/api/v1/urls", json={"target_url": "https://example.com"}, headers=admin_headers
    )
    assert response.headers["x-ratelimit-limit"] == "6"
