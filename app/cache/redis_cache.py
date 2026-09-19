import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime

import orjson
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.domain import Link

logger = logging.getLogger(__name__)

# Bump the version when the cached payload shape changes. Old entries are then
# ignored and expire by TTL, so a deploy never has to flush the cache.
KEY_PREFIX = "link:v1:"
_TOMBSTONE = b"-"

# Errors that mean "Redis is unavailable": degrade to the database, never fail the request.
REDIS_ERRORS = (RedisError, OSError, TimeoutError)


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """A cache hit. ``link is None`` is a *negative* hit: the code is known not to exist."""

    link: Link | None


class LinkCache:
    """Cache-aside store for short-code lookups.

    - Positive entries live for ``cache_ttl_seconds`` ± jitter. The jitter spreads out
      expiries of links cached together (e.g. after a deploy), so they aren't all
      refetched from PostgreSQL at the same moment.
    - Negative entries (unknown codes) live briefly. They absorb scans and typos
      without hammering PostgreSQL, and are invalidated when the code is created.
    - Writers *delete* the key after committing instead of writing the new value.
      Deleting is idempotent and can't race another writer into caching an older
      value.
    """

    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._ttl = settings.cache_ttl_seconds
        self._jitter = settings.cache_ttl_jitter
        self._negative_ttl = settings.cache_negative_ttl_seconds

    async def get(self, short_code: str) -> CacheEntry | None:
        try:
            raw = await self._redis.get(KEY_PREFIX + short_code)
        except REDIS_ERRORS as exc:
            logger.warning("link cache read failed", extra={"error": repr(exc)})
            return None
        if raw is None:
            return None
        if raw == _TOMBSTONE:
            return CacheEntry(link=None)
        return CacheEntry(link=_decode(raw))

    async def set(self, short_code: str, link: Link | None) -> None:
        if link is None:
            value, ttl = _TOMBSTONE, self._negative_ttl
        else:
            value, ttl = _encode(link), self.jittered_ttl()
        try:
            await self._redis.set(KEY_PREFIX + short_code, value, ex=ttl)
        except REDIS_ERRORS as exc:
            logger.warning("link cache write failed", extra={"error": repr(exc)})

    async def invalidate(self, short_code: str) -> None:
        try:
            await self._redis.delete(KEY_PREFIX + short_code)
        except REDIS_ERRORS as exc:
            # The entry will still expire via TTL; the staleness window is bounded.
            logger.error("link cache invalidation failed", extra={"error": repr(exc)})

    def jittered_ttl(self) -> int:
        spread = self._ttl * self._jitter
        return max(1, round(self._ttl + random.uniform(-spread, spread)))  # noqa: S311


def _encode(link: Link) -> bytes:
    expires = link.expires_at.timestamp() if link.expires_at else None
    return orjson.dumps([link.url_id, link.target_url, link.is_active, expires])


def _decode(raw: bytes | str) -> Link:
    url_id, target_url, is_active, expires = orjson.loads(raw)
    expires_at = datetime.fromtimestamp(expires, UTC) if expires is not None else None
    return Link(url_id=url_id, target_url=target_url, is_active=is_active, expires_at=expires_at)
