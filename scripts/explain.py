"""Capture EXPLAIN (ANALYZE, BUFFERS) plans for the queries on hot paths.

    uv run python -m scripts.explain > docs/plans/<label>.txt

Instead of copying SQL by hand, this runs the real service code and records every
statement it sends (via a SQLAlchemy event hook), then re-executes each one under
EXPLAIN with the same parameters. The plans always match what the app runs.
"""

import asyncio
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.repositories import urls as url_repo
from app.schemas.analytics import Granularity
from app.services.analytics import AnalyticsService


async def main() -> None:
    engine = create_async_engine(Settings().database_url)
    captured: list[tuple[str, Any]] = []

    def capture(_c: Connection, _cur: Any, statement: str, params: Any, *_: Any) -> None:
        captured.append((statement, params))

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT u.id, u.short_code, count(*) AS clicks FROM click_events c "
                    "JOIN urls u ON u.id = c.url_id GROUP BY u.id ORDER BY clicks DESC LIMIT 1"
                )
            )
        ).one()
        print(f"-- hottest link: {row.short_code} (id={row.id}, {row.clicks:,} clicks)\n")

        event.listen(conn.sync_connection, "before_cursor_execute", capture)
        session = AsyncSession(bind=conn)
        await url_repo.get_by_code(session, row.short_code)
        await AnalyticsService(session).summarize(
            url_id=row.id,
            short_code=row.short_code,
            granularity=Granularity.DAY,
            start=None,
            end=None,
        )
        event.remove(conn.sync_connection, "before_cursor_execute", capture)

        for statement, params in captured:
            print("=" * 100)
            print(statement.strip())
            print(f"-- params: {params}\n")
            result = await conn.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, SETTINGS) " + statement, params
            )
            for (line,) in result:
                print(line)
            print()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
