import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app import models  # noqa: F401  (registers every table on Base.metadata)
from app.core.config import Settings
from app.core.container import Container
from app.db.base import Base
from app.main import create_app
from tests.utils import login, register_and_login

TEST_DATABASE_URL = os.environ.get(
    "APP_DATABASE_URL",
    "postgresql+asyncpg://shortener:shortener@localhost:5432/shortener_test",
)


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> None:
    """Rebuild the schema from scratch through Alembic, which also validates the migrations.

    Dropping the schema (instead of ``alembic downgrade base``) keeps this working
    when the database was last migrated from another branch, with revisions this
    checkout doesn't know about. CI separately checks that downgrades work.
    """
    asyncio.run(_drop_schema())
    env = {**os.environ, "APP_DATABASE_URL": TEST_DATABASE_URL}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, env=env)


async def _drop_schema() -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


@pytest.fixture
def settings() -> Settings:
    return Settings(environment="test", database_url=TEST_DATABASE_URL)


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with LifespanManager(application):
        yield application
        await _reset_state(application.state.container)


async def _reset_state(container: Container) -> None:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with container.engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def container(app: FastAPI) -> Container:
    return app.state.container  # type: ignore[no-any-return]


@pytest.fixture
async def session(container: Container) -> AsyncIterator[AsyncSession]:
    async with container.sessionmaker() as db_session:
        yield db_session


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
async def user_headers(client: AsyncClient) -> dict[str, str]:
    return await register_and_login(client, "alice@example.com")


@pytest.fixture
async def admin_headers(client: AsyncClient, session: AsyncSession) -> dict[str, str]:
    await register_and_login(client, "root@example.com")
    await session.execute(text("UPDATE users SET role = 'admin' WHERE email = 'root@example.com'"))
    await session.commit()
    # Log in again: the role is embedded in the access token at issue time.
    return await login(client, "root@example.com")
