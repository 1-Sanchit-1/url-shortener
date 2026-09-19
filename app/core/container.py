from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.session import create_engine, create_sessionmaker


@dataclass(slots=True)
class Container:
    """Process-wide resources, created once in the app lifespan and injected per request.

    Keeping every long-lived resource here (instead of module-level globals) makes
    startup/shutdown explicit and lets tests build an isolated container per test.
    """

    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]

    @classmethod
    async def create(cls, settings: Settings) -> "Container":
        engine = create_engine(settings)
        return cls(settings=settings, engine=engine, sessionmaker=create_sessionmaker(engine))

    async def aclose(self) -> None:
        await self.engine.dispose()
