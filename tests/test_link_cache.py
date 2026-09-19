from collections.abc import AsyncIterator

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis_cache import KEY_PREFIX, LinkCache
from app.core.config import Settings
from app.core.container import Container
from app.main import create_app

Headers = dict[str, str]


async def _create(client: AsyncClient, headers: Headers, alias: str, target: str) -> None:
    response = await client.post(
        "/api/v1/urls", json={"target_url": target, "custom_alias": alias}, headers=headers
    )
    assert response.status_code == 201


async def test_redirect_is_served_from_cache(
    client: AsyncClient, user_headers: Headers, container: Container, session: AsyncSession
) -> None:
    await _create(client, user_headers, "cached", "https://example.com/v1")
    assert (await client.get("/cached")).status_code == 302
    assert await container.redis.exists(KEY_PREFIX + "cached")

    # Change the row behind the cache's back: the cached value keeps being served.
    await session.execute(text("UPDATE urls SET target_url = 'https://example.com/v2'"))
    await session.commit()
    assert (await client.get("/cached")).headers["location"] == "https://example.com/v1"


async def test_update_invalidates_cache(
    client: AsyncClient, user_headers: Headers, container: Container
) -> None:
    await _create(client, user_headers, "moving", "https://example.com/old")
    await client.get("/moving")  # warm the cache

    await client.patch(
        "/api/v1/urls/moving", json={"target_url": "https://example.com/new"}, headers=user_headers
    )
    assert not await container.redis.exists(KEY_PREFIX + "moving")
    assert (await client.get("/moving")).headers["location"] == "https://example.com/new"


async def test_delete_invalidates_cache(client: AsyncClient, user_headers: Headers) -> None:
    await _create(client, user_headers, "doomed", "https://example.com")
    await client.get("/doomed")
    await client.delete("/api/v1/urls/doomed", headers=user_headers)
    assert (await client.get("/doomed")).status_code == 404


async def test_unknown_codes_are_negatively_cached_until_created(
    client: AsyncClient, user_headers: Headers, container: Container
) -> None:
    assert (await client.get("/soon")).status_code == 404
    assert await container.redis.get(KEY_PREFIX + "soon") == b"-"

    await _create(client, user_headers, "soon", "https://example.com/soon")
    assert (await client.get("/soon")).status_code == 302


def test_ttl_jitter_stays_within_bounds(settings: Settings) -> None:
    cache = LinkCache(redis=None, settings=settings)  # type: ignore[arg-type]
    ttls = {cache.jittered_ttl() for _ in range(500)}
    low = settings.cache_ttl_seconds * (1 - settings.cache_ttl_jitter)
    high = settings.cache_ttl_seconds * (1 + settings.cache_ttl_jitter)
    assert all(low <= t <= high for t in ttls)
    assert len(ttls) > 50  # actually spread out


@pytest.fixture
async def client_without_redis(settings: Settings) -> AsyncIterator[AsyncClient]:
    broken = settings.model_copy(update={"redis_url": "redis://127.0.0.1:1/0"})
    app = create_app(broken)
    transport = ASGITransport(app=app)
    async with (
        LifespanManager(app),
        AsyncClient(transport=transport, base_url="http://test") as http,
    ):
        yield http


async def test_redirects_survive_redis_outage(
    client: AsyncClient, user_headers: Headers, client_without_redis: AsyncClient
) -> None:
    await _create(client, user_headers, "resilient", "https://example.com/ok")
    response = await client_without_redis.get("/resilient")
    assert response.status_code == 302
    ready = await client_without_redis.get("/health/ready")
    assert ready.status_code == 503
    assert ready.json()["checks"]["redis"].startswith("error")
