from app.cache.local import LocalCache


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(clock: FakeClock, max_entries: int = 3) -> LocalCache[str]:
    return LocalCache[str](
        max_entries=max_entries, ttl_seconds=5, stale_grace_seconds=60, clock=clock
    )


def test_fresh_then_stale_then_gone() -> None:
    clock = FakeClock()
    cache = make(clock)
    cache.put("a", "value")

    assert cache.get("a") == "value"
    clock.now += 6
    assert cache.get("a") is None  # past TTL
    assert cache.get_stale("a") == "value"  # but within grace
    clock.now += 60
    assert cache.get_stale("a") is None


def test_lru_eviction_keeps_recently_used() -> None:
    clock = FakeClock()
    cache = make(clock, max_entries=2)
    cache.put("a", "1")
    cache.put("b", "2")
    cache.get("a")  # touch a, so b is least recently used
    cache.put("c", "3")

    assert cache.get("a") == "1"
    assert cache.get("b") is None
    assert cache.get("c") == "3"
    assert len(cache) == 2


def test_evict_and_clear() -> None:
    cache = make(FakeClock())
    cache.put("a", "1")
    cache.put("b", "2")
    cache.evict("a")
    assert cache.get("a") is None
    cache.clear()
    assert len(cache) == 0
