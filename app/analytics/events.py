import asyncio
import contextlib
import hashlib
import hmac
import logging
import time
from collections import deque
from urllib.parse import urlsplit

from redis.asyncio import Redis
from starlette.requests import Request

from app.cache.redis_cache import REDIS_ERRORS
from app.core.config import Settings
from app.observability.metrics import CLICK_EVENTS_PUBLISHED

logger = logging.getLogger(__name__)

MAX_USER_AGENT = 256
MAX_REFERRER_HOST = 255

ClickEventFields = dict[str, str]


class ClickPublisher:
    """Buffer click events in memory and ship them to a Redis Stream in batches.

    The first version issued one XADD per redirect as a response background task.
    Profiling showed that single round trip was ~40% of the API's CPU per redirect,
    mostly redis-py command encoding, reply parsing and the extra task. Now a
    redirect only appends to a deque (about a microsecond), and a flusher task
    sends everything buffered as one pipelined round trip every
    ``click_flush_interval_ms``.

    Trade-offs, all deliberate:
    - A hard crash loses up to one flush interval of clicks. Graceful shutdown
      flushes the buffer.
    - The buffer is bounded: if Redis is down long enough to fill it, new clicks
      are dropped and counted instead of growing memory without limit.
    - Redirect availability never depends on analytics.
    """

    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._stream = settings.click_stream_name
        self._maxlen = settings.click_stream_maxlen
        self._salt = settings.analytics_salt.get_secret_value().encode()
        self._interval = settings.click_flush_interval_ms / 1000
        self._max_batch = settings.click_flush_max_batch
        self._buffer: deque[ClickEventFields] = deque()
        self._capacity = settings.click_buffer_capacity
        self._task: asyncio.Task[None] | None = None

    def build_event(self, url_id: int, request: Request) -> ClickEventFields:
        client_ip = request.client.host if request.client else ""
        return {
            "url_id": str(url_id),
            "ts": str(int(time.time() * 1000)),
            "ref": _referrer_host(request.headers.get("referer")),
            "ua": request.headers.get("user-agent", "")[:MAX_USER_AGENT],
            "vh": self._visitor_hash(client_ip) if client_ip else "",
        }

    def enqueue(self, event: ClickEventFields) -> None:
        if len(self._buffer) >= self._capacity:
            CLICK_EVENTS_PUBLISHED.labels("dropped").inc()
            return
        self._buffer.append(event)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="click-publisher")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.flush()

    async def flush(self) -> None:
        """Send everything currently buffered, in pipelines of up to ``max_batch``."""
        while self._buffer:
            batch = [self._buffer.popleft() for _ in range(min(len(self._buffer), self._max_batch))]
            pipe = self._redis.pipeline(transaction=False)
            for event in batch:
                # Approximate trimming (~) lets Redis drop whole macro-nodes, which
                # is much cheaper than exact trimming on every XADD.
                pipe.xadd(
                    self._stream,
                    event,  # type: ignore[arg-type]
                    maxlen=self._maxlen,
                    approximate=True,
                )
            try:
                await pipe.execute()
                CLICK_EVENTS_PUBLISHED.labels("ok").inc(len(batch))
            except REDIS_ERRORS as exc:
                CLICK_EVENTS_PUBLISHED.labels("dropped").inc(len(batch))
                logger.warning(
                    "click events dropped", extra={"count": len(batch), "error": repr(exc)}
                )
                return

    def __len__(self) -> int:
        return len(self._buffer)

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self.flush()

    def _visitor_hash(self, client_ip: str) -> str:
        return hmac.new(self._salt, client_ip.encode(), hashlib.sha256).hexdigest()[:16]


def _referrer_host(referer: str | None) -> str:
    if not referer:
        return ""
    try:
        return (urlsplit(referer).hostname or "")[:MAX_REFERRER_HOST]
    except ValueError:
        return ""
