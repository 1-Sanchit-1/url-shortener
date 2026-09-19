from dataclasses import dataclass

from redis.asyncio import BlockingConnectionPool, Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.cache.redis_cache import LinkCache
from app.core.config import Settings
from app.db.session import create_engine, create_sessionmaker
from app.services.links import LinkResolver


@dataclass(slots=True)
class Container:
    """Process-wide resources, created once in the app lifespan and injected per request.

    Keeping every long-lived resource here (instead of module-level globals) makes
    startup/shutdown explicit and lets tests build an isolated container per test.
    """

    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    redis: Redis
    link_cache: LinkCache
    resolver: LinkResolver

    @classmethod
    async def create(cls, settings: Settings) -> "Container":
        engine = create_engine(settings)
        sessionmaker = create_sessionmaker(engine)
        redis = create_redis(settings)
        link_cache = LinkCache(redis, settings)
        return cls(
            settings=settings,
            engine=engine,
            sessionmaker=sessionmaker,
            redis=redis,
            link_cache=link_cache,
            resolver=LinkResolver(link_cache, sessionmaker),
        )

    async def aclose(self) -> None:
        await self.redis.aclose()
        await self.engine.dispose()


def create_redis(settings: Settings) -> Redis:
    # A blocking pool waits briefly for a free connection under bursts instead of
    # failing immediately with "Too many connections".
    pool = BlockingConnectionPool.from_url(
        settings.redis_url,
        max_connections=settings.redis_max_connections,
        timeout=settings.redis_socket_timeout_seconds,
        socket_timeout=settings.redis_socket_timeout_seconds,
        socket_connect_timeout=settings.redis_socket_timeout_seconds,
        health_check_interval=30,
    )
    return Redis(connection_pool=pool)
