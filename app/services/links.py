import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache.invalidation import InvalidationBus
from app.cache.local import LocalCache
from app.cache.redis_cache import CacheEntry, LinkCache
from app.cache.singleflight import SingleFlight
from app.domain import Link
from app.observability.metrics import CACHE_LOOKUPS, DB_LOOKUPS, STALE_SERVED
from app.repositories import urls as url_repo

logger = logging.getLogger(__name__)


class LinkResolver:
    """Resolve short codes through three tiers:

    1. L1: an in-process LRU (microseconds). Absorbs hot keys.
    2. L2: Redis, shared by all processes (sub-millisecond). Absorbs the long tail.
    3. PostgreSQL, the source of truth (milliseconds).

    Misses are coalesced per key, so one expiring hot key produces one database
    query, not one per concurrent request. If both Redis and PostgreSQL fail, a
    recently expired L1 entry is served (stale-if-error) instead of an error.
    """

    def __init__(
        self,
        *,
        local: LocalCache[CacheEntry],
        shared: LinkCache,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        self._local = local
        self._shared = shared
        self._sessionmaker = sessionmaker
        self._flight: SingleFlight[Link | None] = SingleFlight()
        self._bus: InvalidationBus | None = None

    def attach_bus(self, bus: InvalidationBus) -> None:
        self._bus = bus

    async def resolve(self, short_code: str) -> Link | None:
        entry = self._local.get(short_code)
        if entry is not None:
            CACHE_LOOKUPS.labels("l1", "hit" if entry.link else "negative_hit").inc()
            return entry.link
        CACHE_LOOKUPS.labels("l1", "miss").inc()
        return await self._flight.do(short_code, lambda: self._resolve_miss(short_code))

    async def invalidate(self, short_code: str) -> None:
        """Called after a write commits: drop the key from every tier on every process."""
        self._local.evict(short_code)
        await self._shared.invalidate(short_code)
        if self._bus is not None:
            await self._bus.publish(short_code)

    def evict_local(self, short_code: str) -> None:
        self._local.evict(short_code)

    def clear_local(self) -> None:
        self._local.clear()

    async def _resolve_miss(self, short_code: str) -> Link | None:
        entry = await self._shared.get(short_code)
        if entry is None:
            try:
                link = await self._load(short_code)
            except (SQLAlchemyError, OSError):
                stale = self._local.get_stale(short_code)
                if stale is None:
                    raise
                STALE_SERVED.inc()
                logger.warning(
                    "database unavailable; serving stale link", extra={"code": short_code}
                )
                return stale.link
            await self._shared.set(short_code, link)
            entry = CacheEntry(link)
        self._local.put(short_code, entry)
        return entry.link

    async def _load(self, short_code: str) -> Link | None:
        DB_LOOKUPS.inc()
        async with self._sessionmaker() as session:
            url = await url_repo.get_by_code(session, short_code)
        if url is None:
            return None
        return Link(
            url_id=url.id,
            target_url=url.target_url,
            is_active=url.is_active,
            expires_at=url.expires_at,
        )
