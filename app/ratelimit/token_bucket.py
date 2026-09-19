"""Distributed token-bucket rate limiting backed by Redis.

Why a token bucket: it allows short bursts up to ``capacity`` while enforcing a
long-run average of ``refill_per_second``. That matches real clients, which
open a page and fire several requests at once. A fixed window allows 2x the limit
across a window boundary. A sliding log is exact but stores one entry per
request; the bucket stores two numbers per client.

Why Lua: read-refill-decrement must be atomic. Done as separate commands, two API
processes could both read "1 token left" and both allow a request. Redis runs a
script without interleaving other commands. Time comes from the Redis server's
clock (``TIME``), so skew between API hosts can't mint extra tokens.
"""

import logging
import math
from dataclasses import dataclass

from redis.asyncio import Redis

from app.cache.redis_cache import REDIS_ERRORS
from app.ratelimit.policy import RateLimitPolicy

logger = logging.getLogger(__name__)

_BUCKET_SCRIPT = """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])

local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)

local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil then
  tokens = capacity
  ts = now
end

tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate / 1000)

local allowed = 0
local retry_after_ms = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  retry_after_ms = math.ceil((cost - tokens) * 1000 / rate)
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
-- An idle bucket refills completely after capacity/rate seconds; after that it is
-- indistinguishable from a missing key, so let Redis reclaim the memory.
redis.call('PEXPIRE', KEYS[1], math.ceil(capacity * 1000 / rate) + 1000)
return {allowed, tostring(tokens), retry_after_ms}
"""


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


class TokenBucketLimiter:
    def __init__(self, redis: Redis) -> None:
        # register_script sends EVALSHA and falls back to EVAL only if Redis doesn't
        # have the script cached, so the script body isn't resent on every call.
        self._script = redis.register_script(_BUCKET_SCRIPT)

    async def acquire(self, key: str, policy: RateLimitPolicy, cost: int = 1) -> Decision:
        try:
            allowed, tokens, retry_after_ms = await self._script(
                keys=[f"rl:{key}"], args=[policy.capacity, policy.refill_per_second, cost]
            )
        except REDIS_ERRORS as exc:
            # Fail open: a Redis outage should degrade protection, not availability.
            # For endpoints where abuse is costlier than downtime (e.g. paid APIs),
            # failing closed would be the right call instead.
            logger.warning("rate limiter unavailable; allowing request", extra={"error": repr(exc)})
            return Decision(True, policy.capacity, policy.capacity, 0)
        return Decision(
            allowed=bool(allowed),
            limit=policy.capacity,
            remaining=math.floor(float(tokens)),
            retry_after_seconds=math.ceil(int(retry_after_ms) / 1000),
        )
