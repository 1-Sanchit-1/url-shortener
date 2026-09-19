import asyncio
from typing import Any

from app.analytics.events import ClickPublisher
from app.core.config import Settings
from app.core.container import Container, create_redis


def _event(n: int) -> dict[str, str]:
    return {"url_id": str(n), "ts": "1700000000000", "ref": "", "ua": "", "vh": ""}


async def test_background_flush_ships_buffered_events(
    container: Container, settings: Settings
) -> None:
    publisher = container.click_publisher
    for i in range(3):
        publisher.enqueue(_event(i))
    assert len(publisher) == 3

    await asyncio.sleep(settings.click_flush_interval_ms / 1000 * 3)
    assert len(publisher) == 0
    assert await container.redis.xlen(settings.click_stream_name) == 3


async def test_flush_splits_large_buffers_into_batches(
    container: Container, settings: Settings
) -> None:
    small = settings.model_copy(update={"click_flush_max_batch": 10})
    publisher = ClickPublisher(container.redis, small)
    for i in range(25):
        publisher.enqueue(_event(i))
    await publisher.flush()
    entries: Any = await container.redis.xrange(settings.click_stream_name)
    assert [int(fields[b"url_id"]) for _, fields in entries] == list(range(25))


async def test_buffer_is_bounded(container: Container, settings: Settings) -> None:
    tiny = settings.model_copy(update={"click_buffer_capacity": 5})
    publisher = ClickPublisher(container.redis, tiny)
    for i in range(8):
        publisher.enqueue(_event(i))
    assert len(publisher) == 5


async def test_stop_flushes_remaining_events(container: Container, settings: Settings) -> None:
    slow = settings.model_copy(update={"click_flush_interval_ms": 60_000})
    publisher = ClickPublisher(container.redis, slow)
    publisher.start()
    publisher.enqueue(_event(1))
    await publisher.stop()
    assert await container.redis.xlen(settings.click_stream_name) == 1


async def test_redis_outage_drops_batch_without_raising(settings: Settings) -> None:
    broken = create_redis(settings.model_copy(update={"redis_url": "redis://127.0.0.1:1/0"}))
    publisher = ClickPublisher(broken, settings)
    publisher.enqueue(_event(1))
    try:
        await publisher.flush()
        assert len(publisher) == 0
    finally:
        await broken.aclose()
