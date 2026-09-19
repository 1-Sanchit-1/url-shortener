import hashlib
import hmac
import logging
import time
from urllib.parse import urlsplit

from redis.asyncio import Redis
from starlette.requests import Request

from app.cache.redis_cache import REDIS_ERRORS
from app.core.config import Settings
from app.observability.metrics import CLICK_EVENTS_PUBLISHED

logger = logging.getLogger(__name__)

MAX_USER_AGENT = 256
MAX_REFERRER_HOST = 255


class ClickPublisher:
    """Append click events to a Redis Stream; a separate worker persists them.

    The redirect must not wait on an INSERT into a large, heavily indexed table.
    Publishing to a stream costs one sub-millisecond XADD, and it runs as a
    background task *after* the 302 has been sent. The stream also buffers load
    spikes, so the consumer can batch inserts at its own pace.

    Delivery trade-off: if Redis is down, clicks are dropped (logged, counted)
    rather than failing redirects. Analytics are best-effort; redirects are not.
    """

    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._stream = settings.click_stream_name
        self._maxlen = settings.click_stream_maxlen
        self._salt = settings.analytics_salt.get_secret_value().encode()

    def build_event(self, url_id: int, request: Request) -> dict[str, str]:
        client_ip = request.client.host if request.client else ""
        return {
            "url_id": str(url_id),
            "ts": str(int(time.time() * 1000)),
            "ref": _referrer_host(request.headers.get("referer")),
            "ua": request.headers.get("user-agent", "")[:MAX_USER_AGENT],
            "vh": self._visitor_hash(client_ip) if client_ip else "",
        }

    async def publish(self, event: dict[str, str]) -> None:
        try:
            # Approximate trimming (~) lets Redis drop whole macro-nodes, which is
            # much cheaper than exact trimming on every XADD.
            await self._redis.xadd(
                self._stream,
                event,  # type: ignore[arg-type]
                maxlen=self._maxlen,
                approximate=True,
            )
            CLICK_EVENTS_PUBLISHED.labels("ok").inc()
        except REDIS_ERRORS as exc:
            CLICK_EVENTS_PUBLISHED.labels("dropped").inc()
            logger.warning("click event dropped", extra={"error": repr(exc)})

    def _visitor_hash(self, client_ip: str) -> str:
        return hmac.new(self._salt, client_ip.encode(), hashlib.sha256).hexdigest()[:16]


def _referrer_host(referer: str | None) -> str:
    if not referer:
        return ""
    try:
        return (urlsplit(referer).hostname or "")[:MAX_REFERRER_HOST]
    except ValueError:
        return ""
