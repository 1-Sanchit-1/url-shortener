import logging
import random
import re
import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability.metrics import HTTP_IN_PROGRESS, HTTP_LATENCY, HTTP_REQUESTS

logger = logging.getLogger("app.access")

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_QUIET_ROUTES = {"/metrics", "/health/live", "/health/ready"}


class ObservabilityMiddleware:
    """Request id propagation, access logging and RED metrics for every request.

    Written as plain ASGI rather than Starlette's ``BaseHTTPMiddleware``, which
    wraps each request in extra tasks and memory streams. That overhead shows up in
    tail latency on a sub-millisecond redirect path.
    """

    def __init__(self, app: ASGIApp, sample_rate: float = 1.0, slow_ms: float = 250.0) -> None:
        self.app = app
        self.sample_rate = sample_rate
        self.slow_ms = slow_ms

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        status_code = 500
        start = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        HTTP_IN_PROGRESS.inc()
        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            HTTP_IN_PROGRESS.dec()
            elapsed = time.perf_counter() - start
            # Label by route *template* ("/{short_code}"), never the raw path:
            # one time series per short code would explode Prometheus cardinality.
            route = getattr(scope.get("route"), "path", None) or "unmatched"
            method = scope["method"]
            HTTP_REQUESTS.labels(method, route, str(status_code)).inc()
            HTTP_LATENCY.labels(method, route).observe(elapsed)
            if route not in _QUIET_ROUTES and self._should_log(status_code, elapsed):
                logger.info(
                    "request",
                    extra={
                        "method": method,
                        "path": scope["path"],
                        "route": route,
                        "status": status_code,
                        "duration_ms": round(elapsed * 1000, 2),
                    },
                )

    def _should_log(self, status_code: int, elapsed: float) -> bool:
        # Formatting and writing a JSON line costs ~50us, roughly 15% of a cached
        # redirect. At thousands of redirects per second, sampling the boring ones
        # keeps the logs useful for errors and outliers without paying that on
        # every request.
        if status_code >= 400 or elapsed * 1000 >= self.slow_ms:
            return True
        return self.sample_rate >= 1.0 or random.random() < self.sample_rate  # noqa: S311


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope["headers"]:
        if name == b"x-request-id":
            candidate: str = value.decode("latin-1")
            # Only trust well-formed ids; anything else could inject into log lines.
            return candidate if _VALID_REQUEST_ID.fullmatch(candidate) else None
    return None
