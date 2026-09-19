from dataclasses import dataclass

from app.core.config import Settings


@dataclass(slots=True)
class Container:
    """Process-wide resources, created once in the app lifespan and injected per request.

    Keeping every long-lived resource here (instead of module-level globals) makes
    startup/shutdown explicit and lets tests build an isolated container per test.
    """

    settings: Settings

    @classmethod
    async def create(cls, settings: Settings) -> "Container":
        return cls(settings=settings)

    async def aclose(self) -> None:
        return None
