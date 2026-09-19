from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import admin, auth, health, metrics, redirect, urls
from app.core.config import Settings, get_settings
from app.core.container import Container
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.observability.middleware import ObservabilityMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory. Run with ``uvicorn --factory app.main:create_app``."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = await Container.create(settings)
        app.state.container = container
        try:
            yield
        finally:
            await container.aclose()

    app = FastAPI(title="URL Shortener", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        ObservabilityMiddleware,
        sample_rate=settings.access_log_sample_rate,
        slow_ms=settings.access_log_slow_ms,
    )
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(auth.router)
    app.include_router(urls.router)
    app.include_router(admin.router)
    # The catch-all redirect route must be registered last so it never shadows other paths.
    app.include_router(redirect.router)
    return app
