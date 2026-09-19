from datetime import datetime
from typing import TypedDict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ClickRow(TypedDict):
    event_id: str
    url_id: int
    occurred_at: datetime
    referrer_host: str | None
    user_agent: str | None
    visitor_hash: str | None


# One round trip per batch, with three properties:
# - unnest() passes the batch as six array parameters, so the statement text is
#   the same for every batch size. PostgreSQL and asyncpg reuse one prepared
#   statement, and pg_stat_statements shows one entry instead of one per size.
# - Rollups are computed only from rows the INSERT actually wrote (RETURNING), so
#   a redelivered batch, whose events already exist, adds zero to the counts.
# - Events and rollups commit atomically, so the two can never disagree.
# Rollup rows are upserted in (url_id, bucket) order. Concurrent consumers then
# lock hot rows in the same order and can't deadlock.
_INSERT_CLICKS_AND_ROLLUPS = text(
    """
    WITH inserted AS (
        INSERT INTO click_events
            (event_id, url_id, occurred_at, referrer_host, user_agent, visitor_hash)
        SELECT * FROM unnest(
            CAST(:event_ids AS varchar[]),
            CAST(:url_ids AS bigint[]),
            CAST(:occurred_ats AS timestamptz[]),
            CAST(:referrer_hosts AS varchar[]),
            CAST(:user_agents AS varchar[]),
            CAST(:visitor_hashes AS varchar[])
        )
        ON CONFLICT (event_id) DO NOTHING
        RETURNING url_id, occurred_at
    )
    INSERT INTO click_rollups_hourly AS r (url_id, bucket_start, clicks)
    SELECT url_id, date_trunc('hour', occurred_at, 'UTC') AS bucket, count(*)
    FROM inserted
    GROUP BY 1, 2
    ORDER BY 1, 2
    ON CONFLICT (url_id, bucket_start) DO UPDATE SET clicks = r.clicks + EXCLUDED.clicks
    """
)


async def persist_clicks(session: AsyncSession, rows: list[ClickRow]) -> None:
    await session.execute(
        _INSERT_CLICKS_AND_ROLLUPS,
        {
            "event_ids": [r["event_id"] for r in rows],
            "url_ids": [r["url_id"] for r in rows],
            "occurred_ats": [r["occurred_at"] for r in rows],
            "referrer_hosts": [r["referrer_host"] for r in rows],
            "user_agents": [r["user_agent"] for r in rows],
            "visitor_hashes": [r["visitor_hash"] for r in rows],
        },
    )
    await session.commit()
