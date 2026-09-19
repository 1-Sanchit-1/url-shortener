from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.urls import insert_with_alias


async def test_shorten_generates_code(client: AsyncClient) -> None:
    response = await client.post("/api/v1/urls", json={"target_url": "https://example.com/a"})
    assert response.status_code == 201
    body = response.json()
    assert len(body["short_code"]) == 7
    assert body["short_url"].endswith("/" + body["short_code"])
    assert body["target_url"] == "https://example.com/a"
    assert body["is_custom"] is False


async def test_shorten_with_custom_alias(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/urls", json={"target_url": "https://example.com", "custom_alias": "launch"}
    )
    assert response.status_code == 201
    assert response.json()["short_code"] == "launch"
    assert response.json()["is_custom"] is True


async def test_duplicate_alias_conflicts(client: AsyncClient) -> None:
    payload = {"target_url": "https://example.com", "custom_alias": "dupe"}
    assert (await client.post("/api/v1/urls", json=payload)).status_code == 201
    response = await client.post("/api/v1/urls", json=payload)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "alias_taken"


@pytest.mark.parametrize(
    "payload",
    [
        {"target_url": "not a url"},
        {"target_url": "javascript:alert(1)"},
        {"target_url": "ftp://example.com/file"},
        {"target_url": "https://example.com", "custom_alias": "api"},
        {"target_url": "https://example.com", "custom_alias": "bad alias"},
        {"target_url": "http://localhost:8000/abc"},
        {"target_url": "https://example.com/" + "a" * 2100},
        {"target_url": "https://example.com", "expires_at": "2000-01-01T00:00:00Z"},
    ],
)
async def test_shorten_rejects_invalid_input(client: AsyncClient, payload: dict[str, str]) -> None:
    response = await client.post("/api/v1/urls", json=payload)
    assert response.status_code == 422


async def test_redirect_follows_link(client: AsyncClient) -> None:
    created = await client.post("/api/v1/urls", json={"target_url": "https://example.com/x"})
    code = created.json()["short_code"]

    response = await client.get(f"/{code}")
    assert response.status_code == 302
    assert response.headers["location"] == "https://example.com/x"


async def test_redirect_unknown_code_is_404(client: AsyncClient) -> None:
    assert (await client.get("/nope123")).status_code == 404
    assert (await client.get("/" + "x" * 40)).status_code == 404


async def test_redirect_expired_link_is_410(client: AsyncClient, session: AsyncSession) -> None:
    await insert_with_alias(
        session,
        alias="old-promo",
        target_url="https://example.com",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    await session.commit()
    assert (await client.get("/old-promo")).status_code == 410


async def test_health_routes_not_shadowed_by_redirect(client: AsyncClient) -> None:
    assert (await client.get("/health/live")).status_code == 200
