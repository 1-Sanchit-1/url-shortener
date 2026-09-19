from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache.redis_cache import LinkCache
from app.domain import Link
from app.repositories import urls as url_repo


class LinkResolver:
    """Read-through lookup of short codes: Redis first, PostgreSQL on a miss.

    The resolver is a process-wide singleton that opens a database session only on a
    cache miss. A cache hit never touches the connection pool.
    """

    def __init__(self, cache: LinkCache, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._cache = cache
        self._sessionmaker = sessionmaker

    async def resolve(self, short_code: str) -> Link | None:
        entry = await self._cache.get(short_code)
        if entry is not None:
            return entry.link
        link = await self._load(short_code)
        await self._cache.set(short_code, link)
        return link

    async def _load(self, short_code: str) -> Link | None:
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
