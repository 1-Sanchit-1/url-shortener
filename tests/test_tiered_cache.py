import asyncio
from collections.abc import AsyncIterator

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.cache.local import LocalCache
from app.cache.redis_cache import CacheEntry, LinkCache
from app.core.config import Settings
from app.core.container import Container, create_redis
from app.db.session import create_sessionmaker
from app.domain import Link
from app.main import create_app
from app.services.links import LinkResolver

Headers = dict[str, str]


@pytest.fixture
async def second_instance(settings: Settings) -> AsyncIterator[AsyncClient]:
    """A second API process sharing the same PostgreSQL and Redis."""
    other: FastAPI = create_app(settings)
    async with LifespanManager(other):
        async with asyncio.timeout(2):
            await other.state.container.invalidation_bus.wait_until_subscribed()
        transport = ASGITransport(app=other)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


async def test_invalidation_reaches_other_instances(
    client: AsyncClient,
    container: Container,
    user_headers: Headers,
    second_instance: AsyncClient,
) -> None:
    async with asyncio.timeout(2):
        await container.invalidation_bus.wait_until_subscribed()
    await client.post(
        "/api/v1/urls",
        json={"target_url": "https://example.com/before", "custom_alias": "shared"},
        headers=user_headers,
    )
    # Warm the L1 cache of both instances.
    assert (await client.get("/shared")).headers["location"] == "https://example.com/before"
    assert (await second_instance.get("/shared")).headers["location"].endswith("/before")

    await client.patch(
        "/api/v1/urls/shared",
        json={"target_url": "https://example.com/after"},
        headers=user_headers,
    )

    # Well within the 5s L1 TTL, so only the pub/sub message can explain the update.
    for _ in range(50):
        location = (await second_instance.get("/shared")).headers["location"]
        if location.endswith("/after"):
            break
        await asyncio.sleep(0.02)
    assert location == "https://example.com/after"


async def test_cold_hot_key_hits_database_once(
    client: AsyncClient, container: Container, user_headers: Headers
) -> None:
    await client.post(
        "/api/v1/urls",
        json={"target_url": "https://example.com/viral", "custom_alias": "viral"},
        headers=user_headers,
    )
    resolver = container.resolver
    await resolver.invalidate("viral")

    loads = 0
    original = resolver._load

    async def counting_load(code: str) -> Link | None:
        nonlocal loads
        loads += 1
        await asyncio.sleep(0.01)
        return await original(code)

    resolver._load = counting_load  # type: ignore[method-assign,assignment]
    results = await asyncio.gather(*(resolver.resolve("viral") for _ in range(200)))

    assert {link.target_url for link in results if link} == {"https://example.com/viral"}
    assert loads == 1


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_stale_entry_served_when_redis_and_postgres_are_down(settings: Settings) -> None:
    down = settings.model_copy(
        update={
            "redis_url": "redis://127.0.0.1:1/0",
            "database_url": "postgresql+asyncpg://x:y@127.0.0.1:1/none",
        }
    )
    engine = create_async_engine(down.database_url)
    redis = create_redis(down)
    clock = Clock()
    resolver = LinkResolver(
        local=LocalCache[CacheEntry](
            max_entries=10, ttl_seconds=5, stale_grace_seconds=300, clock=clock
        ),
        shared=LinkCache(redis, down),
        sessionmaker=create_sessionmaker(engine),
    )
    link = Link(url_id=1, target_url="https://example.com", is_active=True, expires_at=None)
    resolver._local.put("abc", CacheEntry(link))
    clock.now += 10  # expired from L1, but within the grace period

    try:
        assert await resolver.resolve("abc") == link
        with pytest.raises(OSError, match=r"Connect call failed|Multiple exceptions"):
            await resolver.resolve("never-seen")
    finally:
        await redis.aclose()
        await engine.dispose()
