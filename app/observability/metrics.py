"""Prometheus metrics.

Defined once at import time on the default registry. The API runs one process per
container and scales by adding containers, so each process exposes its own
/metrics and Prometheus aggregates across targets. Running several uvicorn
workers in one container would need prometheus_client's multiprocess mode instead.
"""

from prometheus_client import Counter, Gauge, Histogram

LATENCY_BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests by route template and status",
    ["method", "route", "status"],
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency by route template",
    ["method", "route"],
    buckets=LATENCY_BUCKETS,
)
HTTP_IN_PROGRESS = Gauge("http_requests_in_progress", "HTTP requests currently being served")

CACHE_LOOKUPS = Counter(
    "link_cache_lookups_total",
    "Short-code lookups per cache tier and outcome",
    ["tier", "result"],  # tier: l1|l2; result: hit|negative_hit|miss|error
)
DB_LOOKUPS = Counter("link_db_lookups_total", "Short-code lookups that reached PostgreSQL")
STALE_SERVED = Counter(
    "link_stale_served_total", "Redirects served from stale L1 entries during an outage"
)
SHORT_CODE_COLLISIONS = Counter(
    "short_code_collisions_total", "Generated short codes that collided with an existing one"
)
RATE_LIMIT_DECISIONS = Counter(
    "rate_limit_decisions_total", "Rate limiter outcomes", ["policy", "result"]
)
CLICK_EVENTS_PUBLISHED = Counter(
    "click_events_published_total", "Click events sent to the stream", ["result"]
)
DB_POOL_CHECKED_OUT = Gauge(
    "db_pool_connections_checked_out", "PostgreSQL connections currently in use"
)

# Worker-side metrics
CLICK_EVENTS_INGESTED = Counter("click_events_ingested_total", "Click events written to PostgreSQL")
CLICK_BATCH_DURATION = Histogram(
    "click_batch_duration_seconds",
    "Time to persist one batch of click events",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
CLICK_STREAM_LAG = Gauge(
    "click_stream_lag_entries", "Stream entries not yet delivered to the consumer group"
)
