import asyncio
import contextlib
import logging
from collections.abc import Callable

from redis.asyncio import Redis

from app.cache.redis_cache import REDIS_ERRORS

logger = logging.getLogger(__name__)

CHANNEL = "link-invalidations"


class InvalidationBus:
    """Broadcast cache evictions to every API process over Redis pub/sub.

    Each process holds its own L1 cache, so a write handled by one process must
    evict the key everywhere. Pub/sub is fire-and-forget: a subscriber that is
    disconnected misses messages. To stay correct, the whole L1 cache is flushed
    after every (re)subscribe, and the short L1 TTL bounds staleness regardless.
    """

    def __init__(
        self,
        redis: Redis,
        *,
        on_invalidate: Callable[[str], None],
        on_reset: Callable[[], None],
    ) -> None:
        self._redis = redis
        self._on_invalidate = on_invalidate
        self._on_reset = on_reset
        self._task: asyncio.Task[None] | None = None
        self._subscribed = asyncio.Event()

    async def publish(self, short_code: str) -> None:
        try:
            await self._redis.publish(CHANNEL, short_code)
        except REDIS_ERRORS as exc:
            logger.error("invalidation publish failed", extra={"error": repr(exc)})

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="link-invalidation-listener")

    async def wait_until_subscribed(self) -> None:
        await self._subscribed.wait()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        backoff = 0.5
        while True:
            try:
                async with self._redis.pubsub(ignore_subscribe_messages=True) as pubsub:
                    await pubsub.subscribe(CHANNEL)
                    self._on_reset()
                    self._subscribed.set()
                    backoff = 0.5
                    while True:
                        message = await pubsub.get_message(timeout=1.0)
                        if message is not None:
                            data = message["data"]
                            self._on_invalidate(data.decode() if isinstance(data, bytes) else data)
            except REDIS_ERRORS as exc:
                self._subscribed.clear()
                logger.warning(
                    "invalidation listener disconnected; retrying",
                    extra={"error": repr(exc), "retry_in": backoff},
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)
