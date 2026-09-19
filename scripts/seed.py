"""Seed a realistic dataset for load testing and query profiling.

    uv run python -m scripts.seed --urls 100000 --clicks 5000000 --reset

Clicks are skewed so a few links are very hot: link popularity roughly follows a
power law, like real traffic. That skew is what makes the analytics queries for
popular links expensive, and it is the case worth optimizing.

Rows are generated inside PostgreSQL with generate_series; streaming millions of
rows from Python would dominate the runtime.
"""

import argparse
import asyncio
import time
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings
from app.core.security import hash_password

OWNER_EMAIL = "loadtest@example.com"
OWNER_PASSWORD = "loadtest-password"  # noqa: S105  (local fixture account)
CODES_FILE = Path(__file__).resolve().parent.parent / "loadtest" / "codes.txt"
CLICK_BATCH = 1_000_000
REFERRERS = (
    "ARRAY['news.ycombinator.com','twitter.com','google.com','reddit.com',"
    "'linkedin.com',NULL,NULL,NULL]"
)


async def seed(n_urls: int, n_clicks: int, days: int, reset: bool) -> None:
    settings = Settings()
    if settings.environment == "prod":
        raise SystemExit("refusing to seed a production database")
    engine = create_async_engine(settings.database_url)
    started = time.perf_counter()

    async with engine.begin() as conn:
        if reset:
            await conn.execute(text("TRUNCATE click_events, urls RESTART IDENTITY CASCADE"))
            print("reset urls and click_events")
        owner_id = await conn.scalar(
            text(
                "INSERT INTO users (email, password_hash) VALUES (:email, :hash) "
                "ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email RETURNING id"
            ),
            {"email": OWNER_EMAIL, "hash": await hash_password(OWNER_PASSWORD)},
        )
        await conn.execute(
            text(
                "INSERT INTO urls (short_code, target_url, owner_id) "
                "SELECT 'ld' || g, 'https://example.com/articles/' || g, :owner "
                "FROM generate_series(1, :n) AS g "
                "ON CONFLICT (short_code) DO NOTHING"
            ),
            {"owner": owner_id, "n": n_urls},
        )
        first_id = await conn.scalar(
            text("SELECT min(id) FROM urls WHERE owner_id = :owner"), {"owner": owner_id}
        )
        print(f"inserted {n_urls:,} urls")

    # One transaction per batch keeps WAL and lock footprints bounded.
    for offset in range(0, n_clicks, CLICK_BATCH):
        batch = min(CLICK_BATCH, n_clicks - offset)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    f"""
                    INSERT INTO click_events
                        (event_id, url_id, occurred_at, referrer_host, user_agent, visitor_hash)
                    SELECT
                        'seed-' || (:offset + g),
                        -- power(random(), 3) skews toward the first ids: link #1 gets
                        -- ~2% of all clicks, the median link a handful.
                        :first_id + floor(:n_urls * power(random(), 3))::bigint,
                        now() - random() * make_interval(days => :days),
                        ({REFERRERS})[1 + floor(random() * 8)::int],
                        'Mozilla/5.0 (seed)',
                        substr(md5((random() * 100000)::int::text), 1, 16)
                    FROM generate_series(1, :batch) AS g
                    """  # noqa: S608  (only trusted constants are interpolated)
                ),
                {
                    "offset": offset,
                    "first_id": first_id,
                    "n_urls": n_urls,
                    "days": days,
                    "batch": batch,
                },
            )
        print(f"inserted {offset + batch:,}/{n_clicks:,} clicks")

    await finalize(engine, owner_id)
    await engine.dispose()

    print(f"wrote {CODES_FILE} (hottest codes first)")
    print(f"owner login: {OWNER_EMAIL} / {OWNER_PASSWORD}")
    print(f"done in {time.perf_counter() - started:.1f}s")


async def finalize(engine: AsyncEngine, owner_id: int) -> None:
    """Refresh planner statistics and export the seeded codes for Locust."""
    # VACUUM cannot run inside a transaction block.
    async with engine.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
        await conn.execute(text("VACUUM ANALYZE urls"))
        await conn.execute(text("VACUUM ANALYZE click_events"))
        result = await conn.execute(
            text("SELECT short_code FROM urls WHERE owner_id = :o ORDER BY id"), {"o": owner_id}
        )
        CODES_FILE.write_text("\n".join(result.scalars()) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--urls", type=int, default=100_000)
    parser.add_argument("--clicks", type=int, default=5_000_000)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--reset", action="store_true", help="truncate urls and clicks first")
    args = parser.parse_args()
    asyncio.run(seed(args.urls, args.clicks, args.days, args.reset))


if __name__ == "__main__":
    main()
