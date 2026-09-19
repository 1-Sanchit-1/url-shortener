from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.ingest import ClickRow, persist_clicks
from app.core.container import Container
from app.workers.click_consumer import ClickConsumer
from tests.utils import register_and_login

Headers = dict[str, str]
DAY0 = datetime(2026, 3, 1, tzinfo=UTC)


async def _link(client: AsyncClient, headers: Headers, alias: str = "stats") -> int:
    response = await client.post(
        "/api/v1/urls",
        json={"target_url": "https://example.com", "custom_alias": alias},
        headers=headers,
    )
    url_id: int = response.json()["id"]
    return url_id


async def _clicks(
    session: AsyncSession, url_id: int, events: list[tuple[datetime, str | None, str]]
) -> None:
    rows = [
        ClickRow(
            event_id=f"{i}-0",
            url_id=url_id,
            occurred_at=ts,
            referrer_host=ref,
            user_agent=None,
            visitor_hash=visitor,
        )
        for i, (ts, ref, visitor) in enumerate(events)
    ]
    await persist_clicks(session, rows)


async def test_daily_series_is_zero_filled(
    client: AsyncClient, user_headers: Headers, session: AsyncSession
) -> None:
    url_id = await _link(client, user_headers)
    await _clicks(
        session,
        url_id,
        [
            (DAY0 + timedelta(hours=1), "news.example", "v1"),
            (DAY0 + timedelta(hours=5), "news.example", "v2"),
            (DAY0 + timedelta(days=2, hours=3), None, "v1"),
            (DAY0 + timedelta(days=9), None, "v3"),  # outside the range
        ],
    )

    response = await client.get(
        "/api/v1/urls/stats/analytics",
        params={"start": "2026-03-01T00:00:00Z", "end": "2026-03-03T12:00:00Z"},
        headers=user_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert [p["clicks"] for p in body["series"]] == [2, 0, 1]
    assert body["series"][0]["bucket"] == "2026-03-01T00:00:00Z"
    assert body["total_clicks"] == 3
    assert body["unique_visitors"] == 2
    assert body["top_referrers"] == [
        {"referrer": "news.example", "clicks": 2},
        {"referrer": "(direct)", "clicks": 1},
    ]


async def test_hourly_series(
    client: AsyncClient, user_headers: Headers, session: AsyncSession
) -> None:
    url_id = await _link(client, user_headers)
    await _clicks(
        session,
        url_id,
        [(DAY0 + timedelta(minutes=m), None, "v") for m in (5, 10, 70, 190)],
    )
    response = await client.get(
        "/api/v1/urls/stats/analytics",
        params={
            "granularity": "hour",
            "start": "2026-03-01T00:00:00Z",
            "end": "2026-03-01T03:30:00Z",
        },
        headers=user_headers,
    )
    assert [p["clicks"] for p in response.json()["series"]] == [2, 1, 0, 1]


async def test_range_limits_are_enforced(client: AsyncClient, user_headers: Headers) -> None:
    await _link(client, user_headers)
    response = await client.get(
        "/api/v1/urls/stats/analytics",
        params={
            "granularity": "hour",
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-03-01T00:00:00Z",
        },
        headers=user_headers,
    )
    assert response.status_code == 422


async def test_analytics_are_private_to_owner(client: AsyncClient, user_headers: Headers) -> None:
    await _link(client, user_headers)
    stranger = await register_and_login(client, "stranger@example.com")
    response = await client.get("/api/v1/urls/stats/analytics", headers=stranger)
    assert response.status_code == 404


async def test_end_to_end_click_is_counted(
    client: AsyncClient, user_headers: Headers, container: Container
) -> None:
    await _link(client, user_headers, alias="e2e")
    await client.get("/e2e", headers={"Referer": "https://social.example/feed"})

    consumer = ClickConsumer(
        container.redis, container.sessionmaker, container.settings, consumer_name="t"
    )
    await consumer.ensure_group()
    await consumer.run_once()

    body = (await client.get("/api/v1/urls/e2e/analytics", headers=user_headers)).json()
    assert body["total_clicks"] == 1
    assert body["top_referrers"] == [{"referrer": "social.example", "clicks": 1}]
