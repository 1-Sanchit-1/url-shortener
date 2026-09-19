"""Click-stream consumer: Redis Stream -> PostgreSQL, in batches.

Run one or more instances with ``python -m app.workers.click_consumer``. Instances
share a consumer group, so Redis splits the stream across them and each entry is
delivered to exactly one consumer.

Delivery is at-least-once. An entry is XACKed only after its batch commits, so a
crash mid-batch leaves the entries pending. Another consumer reclaims them with
XAUTOCLAIM, and the unique ``event_id`` makes the re-insert a no-op.
"""

import asyncio
import contextlib
import logging
import os
import signal
import socket
import time
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache.redis_cache import REDIS_ERRORS
from app.core.config import Settings, get_settings
from app.db.session import create_engine, create_sessionmaker
from app.models import ClickEvent

logger = logging.getLogger(__name__)

Row = dict[str, Any]


class ClickConsumer:
    def __init__(
        self,
        redis: Redis,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        consumer_name: str,
    ) -> None:
        self._redis = redis
        self._sessionmaker = sessionmaker
        self._stream = settings.click_stream_name
        self._group = settings.click_consumer_group
        self._consumer = consumer_name
        self._batch_size = settings.click_batch_size
        self._block_ms = settings.click_block_ms
        self._claim_idle_ms = settings.click_claim_idle_ms
        self._last_claim = 0.0

    async def ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def run_once(self) -> int:
        """Process one batch. Returns the number of stream entries handled."""
        entries = await self._reclaim_stale()
        if not entries:
            # redis-py's stream reply types vary with protocol version; treat as Any.
            response: Any = await self._redis.xreadgroup(
                self._group,
                self._consumer,
                {self._stream: ">"},
                count=self._batch_size,
                block=self._block_ms,
            )
            entries = response[0][1] if response else []
        if not entries:
            return 0

        rows, ids = [], []
        for entry_id, fields in entries:
            ids.append(entry_id)
            row = parse_entry(entry_id, fields)
            if row is None:
                # Poison message: acknowledge it so it isn't redelivered forever.
                logger.error("dropping malformed click event", extra={"entry_id": str(entry_id)})
            else:
                rows.append(row)

        if rows:
            await self._persist(rows)
        await self._redis.xack(self._stream, self._group, *ids)
        return len(ids)

    async def run(self, stop: asyncio.Event) -> None:
        await self.ensure_group()
        logger.info("click consumer started", extra={"consumer": self._consumer})
        while not stop.is_set():
            try:
                await self.run_once()
            except REDIS_ERRORS as exc:
                logger.warning("stream read failed; backing off", extra={"error": repr(exc)})
                await asyncio.sleep(1)
            except Exception:
                # Database errors: entries stay pending and are retried after reclaim.
                logger.exception("batch failed; will retry")
                await asyncio.sleep(1)
        logger.info("click consumer stopped", extra={"consumer": self._consumer})

    async def _persist(self, rows: list[Row]) -> None:
        async with self._sessionmaker() as session:
            stmt = insert(ClickEvent).values(rows)
            await session.execute(stmt.on_conflict_do_nothing(index_elements=["event_id"]))
            await session.commit()

    async def _reclaim_stale(self) -> list[tuple[Any, Any]]:
        """Take over entries a crashed consumer read but never acknowledged."""
        now = time.monotonic()
        if now - self._last_claim < self._claim_idle_ms / 1000:
            return []
        self._last_claim = now
        claimed: Any = await self._redis.xautoclaim(
            self._stream,
            self._group,
            self._consumer,
            min_idle_time=self._claim_idle_ms,
            start_id="0-0",
            count=self._batch_size,
        )
        return list(claimed[1])


def parse_entry(entry_id: bytes | str, fields: dict[bytes, bytes]) -> Row | None:
    try:
        data = {k.decode(): v.decode() for k, v in fields.items()}
        return {
            "event_id": entry_id.decode() if isinstance(entry_id, bytes) else entry_id,
            "url_id": int(data["url_id"]),
            "occurred_at": datetime.fromtimestamp(int(data["ts"]) / 1000, UTC),
            "referrer_host": data.get("ref") or None,
            "user_agent": data.get("ua") or None,
            "visitor_hash": data.get("vh") or None,
        }
    except (KeyError, ValueError, UnicodeDecodeError):
        return None


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    engine = create_engine(settings)
    redis = Redis.from_url(settings.redis_url)
    consumer = ClickConsumer(
        redis,
        create_sessionmaker(engine),
        settings,
        consumer_name=f"{socket.gethostname()}-{os.getpid()}",
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await consumer.run(stop)
    finally:
        with contextlib.suppress(Exception):
            await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
