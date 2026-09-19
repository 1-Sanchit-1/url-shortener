import asyncio

import pytest

from app.cache.singleflight import SingleFlight


async def test_concurrent_calls_share_one_execution() -> None:
    flight: SingleFlight[int] = SingleFlight()
    calls = 0

    async def load() -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return 42

    results = await asyncio.gather(*(flight.do("k", load) for _ in range(100)))
    assert results == [42] * 100
    assert calls == 1
    assert len(flight) == 0


async def test_errors_propagate_to_every_waiter() -> None:
    flight: SingleFlight[int] = SingleFlight()

    async def boom() -> int:
        await asyncio.sleep(0.01)
        raise RuntimeError("db down")

    results = await asyncio.gather(
        *(flight.do("k", boom) for _ in range(5)), return_exceptions=True
    )
    assert all(isinstance(r, RuntimeError) for r in results)
    assert len(flight) == 0


async def test_cancelling_one_waiter_does_not_cancel_the_others() -> None:
    flight: SingleFlight[str] = SingleFlight()

    async def slow() -> str:
        await asyncio.sleep(0.05)
        return "done"

    first = asyncio.create_task(flight.do("k", slow))
    second = asyncio.create_task(flight.do("k", slow))
    await asyncio.sleep(0.01)
    first.cancel()

    assert await second == "done"
    with pytest.raises(asyncio.CancelledError):
        await first


async def test_later_calls_run_again() -> None:
    flight: SingleFlight[int] = SingleFlight()
    counter = iter(range(10))

    async def load() -> int:
        return next(counter)

    assert await flight.do("k", load) == 0
    assert await flight.do("k", load) == 1
