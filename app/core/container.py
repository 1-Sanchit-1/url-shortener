from dataclasses import dataclass

from redis.asyncio import BlockingConnectionPool, Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.analytics.events import ClickPublisher
from app.cache.invalidation import InvalidationBus
from app.cache.local import LocalCache
from app.cache.redis_cache import CacheEntry, LinkCache
from app.core.config import Settings
from app.db.session import create_engine, create_sessionmaker
from app.ratelimit.token_bucket import TokenBucketLimiter
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
    pubsub_redis: Redis
    resolver: LinkResolver
    invalidation_bus: InvalidationBus
    rate_limiter: TokenBucketLimiter
    click_publisher: ClickPublisher

    @classmethod
    async def create(cls, settings: Settings) -> "Container":
        engine = create_engine(settings)
        sessionmaker = create_sessionmaker(engine)
        redis = create_redis(settings)
        # Pub/sub holds a connection open and blocks on reads, so it gets its own
        # client without the short socket timeout used for cache lookups.
        pubsub_redis = Redis.from_url(settings.redis_url, socket_connect_timeout=1.0)

        resolver = LinkResolver(
            local=LocalCache[CacheEntry](
                max_entries=settings.l1_cache_max_entries,
                ttl_seconds=settings.l1_cache_ttl_seconds,
                stale_grace_seconds=settings.l1_stale_grace_seconds,
            ),
            shared=LinkCache(redis, settings),
            sessionmaker=sessionmaker,
        )
        bus = InvalidationBus(
            pubsub_redis, on_invalidate=resolver.evict_local, on_reset=resolver.clear_local
        )
        resolver.attach_bus(bus)
        bus.start()
        return cls(
            settings=settings,
            engine=engine,
            sessionmaker=sessionmaker,
            redis=redis,
            pubsub_redis=pubsub_redis,
            resolver=resolver,
            invalidation_bus=bus,
            rate_limiter=TokenBucketLimiter(redis),
            click_publisher=ClickPublisher(redis, settings),
        )

    async def aclose(self) -> None:
        await self.invalidation_bus.stop()
        await self.pubsub_redis.aclose()
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
