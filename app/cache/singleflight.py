import asyncio
from collections.abc import Awaitable, Callable
from functools import partial


class SingleFlight[T]:
    """Coalesce concurrent calls for the same key into one execution.

    When a hot link's cache entry expires, hundreds of in-flight requests miss at
    once. Without coalescing, each one queries PostgreSQL (a cache stampede). With
    it, one query runs and every waiter shares its result.

    The work runs in its own task and each caller awaits it through ``shield``, so
    a caller that disconnects doesn't cancel the lookup the other callers wait on.
    """

    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Task[T]] = {}

    async def do(self, key: str, fn: Callable[[], Awaitable[T]]) -> T:
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(fn())
            self._inflight[key] = task
            task.add_done_callback(partial(self._forget, key))
        return await asyncio.shield(task)

    def _forget(self, key: str, task: asyncio.Task[T]) -> None:
        if self._inflight.get(key) is task:
            del self._inflight[key]
        if not task.cancelled():
            task.exception()  # mark retrieved even if every waiter went away

    def __len__(self) -> int:
        return len(self._inflight)
