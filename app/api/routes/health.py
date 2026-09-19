import asyncio
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import ContainerDep

router = APIRouter(prefix="/health", tags=["health"])

CHECK_TIMEOUT_SECONDS = 1.0


@router.get("/live")
async def liveness() -> dict[str, str]:
    """Liveness probe: the process is up and the event loop is responsive.

    Deliberately checks no dependencies. A database outage should take pods out of
    rotation (readiness), not trigger a restart storm (liveness).
    """
    return {"status": "ok"}


@router.get("/ready")
async def readiness(container: ContainerDep) -> JSONResponse:
    """Readiness probe: dependencies required to serve traffic are reachable."""

    async def postgres() -> None:
        async with container.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def redis() -> None:
        await container.redis.ping()

    names = ("postgres", "redis")
    results = await asyncio.gather(_check(postgres), _check(redis))
    checks = dict(zip(names, results, strict=True))
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ok" if healthy else "degraded", "checks": checks},
    )


async def _check(probe: Callable[[], Awaitable[None]]) -> str:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            await probe()
    except TimeoutError:
        return "error: timeout"
    except Exception as exc:
        return f"error: {type(exc).__name__}"
    return "ok"
