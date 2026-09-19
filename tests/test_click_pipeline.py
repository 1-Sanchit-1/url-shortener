from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.ingest import ClickRow, persist_clicks
from app.core.config import Settings
from app.core.container import Container
from app.models import ClickEvent, ClickRollupHourly
from app.workers.click_consumer import ClickConsumer, parse_entry

Headers = dict[str, str]


@pytest.fixture
async def consumer(container: Container, settings: Settings) -> ClickConsumer:
    worker = ClickConsumer(
        container.redis, container.sessionmaker, settings, consumer_name="test-consumer"
    )
    await worker.ensure_group()
    return worker


async def _make_link(client: AsyncClient, headers: Headers, alias: str = "tracked") -> None:
    response = await client.post(
        "/api/v1/urls",
        json={"target_url": "https://example.com", "custom_alias": alias},
        headers=headers,
    )
    assert response.status_code == 201


async def _count(session: AsyncSession) -> int:
    return (await session.scalar(select(func.count()).select_from(ClickEvent))) or 0


async def test_redirect_publishes_click_event(
    client: AsyncClient, user_headers: Headers, container: Container, settings: Settings
) -> None:
    await _make_link(client, user_headers)
    await client.get(
        "/tracked",
        headers={"Referer": "https://news.example.org/post/1", "User-Agent": "pytest-agent"},
    )

    entries: Any = await container.redis.xrange(settings.click_stream_name)
    assert len(entries) == 1
    _, fields = entries[0]
    assert fields[b"ref"] == b"news.example.org"
    assert fields[b"ua"] == b"pytest-agent"
    assert len(fields[b"vh"]) == 16  # hashed, never the raw IP
    assert b"127.0.0.1" not in fields.values()


async def test_consumer_persists_and_acknowledges(
    client: AsyncClient,
    user_headers: Headers,
    consumer: ClickConsumer,
    container: Container,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await _make_link(client, user_headers)
    for _ in range(3):
        await client.get("/tracked")

    assert await consumer.run_once() == 3
    assert await _count(session) == 3
    pending = await container.redis.xpending(
        settings.click_stream_name, settings.click_consumer_group
    )
    assert pending["pending"] == 0


async def test_redelivered_entries_are_not_double_counted(
    client: AsyncClient,
    user_headers: Headers,
    container: Container,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await _make_link(client, user_headers)
    await client.get("/tracked")
    await client.get("/tracked")

    # Consumer A reads the entries, persists them, then "crashes" before XACK.
    crashed = ClickConsumer(container.redis, container.sessionmaker, settings, "crashed")
    await crashed.ensure_group()
    response: Any = await container.redis.xreadgroup(
        settings.click_consumer_group, "crashed", {settings.click_stream_name: ">"}, count=10
    )
    entries = response[0][1]
    await crashed._persist([row for eid, f in entries if (row := parse_entry(eid, f)) is not None])

    # Consumer B reclaims the idle pending entries and processes them again.
    rescuer_settings = settings.model_copy(update={"click_claim_idle_ms": 0})
    rescuer = ClickConsumer(container.redis, container.sessionmaker, rescuer_settings, "rescuer")
    assert await rescuer.run_once() == 2

    assert await _count(session) == 2
    pending = await container.redis.xpending(
        settings.click_stream_name, settings.click_consumer_group
    )
    assert pending["pending"] == 0


async def test_malformed_entries_are_dropped_not_retried(
    consumer: ClickConsumer, container: Container, session: AsyncSession, settings: Settings
) -> None:
    await container.redis.xadd(settings.click_stream_name, {"url_id": "not-a-number", "ts": "x"})
    await container.redis.xadd(settings.click_stream_name, {"url_id": "1", "ts": "1700000000000"})

    assert await consumer.run_once() == 2
    assert await _count(session) == 1
    pending = await container.redis.xpending(
        settings.click_stream_name, settings.click_consumer_group
    )
    assert pending["pending"] == 0


async def test_rollups_accumulate_across_batches(session: AsyncSession) -> None:
    hour = datetime(2026, 5, 1, 10, tzinfo=UTC)

    def row(event_id: str, minute: int) -> ClickRow:
        return ClickRow(
            event_id=event_id,
            url_id=7,
            occurred_at=hour + timedelta(minutes=minute),
            referrer_host=None,
            user_agent=None,
            visitor_hash=None,
        )

    await persist_clicks(session, [row("1-0", 1), row("2-0", 59)])
    await persist_clicks(session, [row("3-0", 30), row("4-0", 61)])

    buckets = (
        await session.execute(
            select(ClickRollupHourly.bucket_start, ClickRollupHourly.clicks).order_by(
                ClickRollupHourly.bucket_start
            )
        )
    ).all()
    assert [tuple(b) for b in buckets] == [(hour, 3), (hour + timedelta(hours=1), 1)]
