import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(slots=True)
class _Entry[V]:
    value: V
    fresh_until: float
    stale_until: float


class LocalCache[V]:
    """Bounded in-process LRU cache with a TTL and a stale-if-error grace period.

    This is the L1 tier. It exists mainly for *hot keys*. A viral link is resolved
    thousands of times a second; without L1 every one of those is a round trip to
    the single Redis shard that owns the key. With L1, each worker process asks
    Redis about that key at most once per TTL.

    Not thread-safe by design: it is only touched from one asyncio event loop, and
    no ``await`` happens between a read and its matching write.
    """

    def __init__(
        self,
        *,
        max_entries: int,
        ttl_seconds: float,
        stale_grace_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._data: OrderedDict[str, _Entry[V]] = OrderedDict()
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        self._grace = stale_grace_seconds
        self._clock = clock

    def get(self, key: str) -> V | None:
        """Return a fresh value, or ``None`` if the key is missing or past its TTL."""
        entry = self._data.get(key)
        if entry is None or entry.fresh_until <= self._clock():
            return None
        self._data.move_to_end(key)
        return entry.value

    def get_stale(self, key: str) -> V | None:
        """Return a value past its TTL but within the grace period.

        Used only when the sources of truth are unavailable. Serving a slightly
        stale redirect is better than failing it.
        """
        entry = self._data.get(key)
        if entry is None or entry.stale_until <= self._clock():
            return None
        return entry.value

    def put(self, key: str, value: V) -> None:
        now = self._clock()
        self._data[key] = _Entry(value, now + self._ttl, now + self._ttl + self._grace)
        self._data.move_to_end(key)
        while len(self._data) > self._max_entries:
            self._data.popitem(last=False)

    def evict(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)
