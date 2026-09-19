from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.urls import insert_with_alias
from tests.utils import register_and_login

Headers = dict[str, str]


async def shorten(client: AsyncClient, headers: Headers, **payload: object) -> dict[str, object]:
    payload.setdefault("target_url", "https://example.com/a")
    response = await client.post("/api/v1/urls", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


async def test_shorten_requires_authentication(client: AsyncClient) -> None:
    response = await client.post("/api/v1/urls", json={"target_url": "https://example.com"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_shorten_generates_code(client: AsyncClient, user_headers: Headers) -> None:
    body = await shorten(client, user_headers)
    assert isinstance(body["short_code"], str)
    assert len(body["short_code"]) == 7
    assert str(body["short_url"]).endswith("/" + body["short_code"])
    assert body["target_url"] == "https://example.com/a"
    assert body["is_custom"] is False


async def test_shorten_with_custom_alias(client: AsyncClient, user_headers: Headers) -> None:
    body = await shorten(client, user_headers, custom_alias="launch")
    assert body["short_code"] == "launch"
    assert body["is_custom"] is True


async def test_duplicate_alias_conflicts(client: AsyncClient, user_headers: Headers) -> None:
    await shorten(client, user_headers, custom_alias="dupe")
    response = await client.post(
        "/api/v1/urls",
        json={"target_url": "https://example.com", "custom_alias": "dupe"},
        headers=user_headers,
    )
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
async def test_shorten_rejects_invalid_input(
    client: AsyncClient, user_headers: Headers, payload: dict[str, str]
) -> None:
    response = await client.post("/api/v1/urls", json=payload, headers=user_headers)
    assert response.status_code == 422


async def test_redirect_follows_link(client: AsyncClient, user_headers: Headers) -> None:
    body = await shorten(client, user_headers, target_url="https://example.com/x")
    response = await client.get(f"/{body['short_code']}")
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


async def test_list_is_scoped_to_owner_and_paginated(
    client: AsyncClient, user_headers: Headers
) -> None:
    other = await register_and_login(client, "bob@example.com")
    await shorten(client, other, custom_alias="bobs-link")
    for i in range(5):
        await shorten(client, user_headers, custom_alias=f"alice-{i}")

    first = (await client.get("/api/v1/urls?limit=3", headers=user_headers)).json()
    assert [u["short_code"] for u in first["items"]] == ["alice-4", "alice-3", "alice-2"]
    assert first["next_cursor"] is not None

    second = (
        await client.get(
            f"/api/v1/urls?limit=3&cursor={first['next_cursor']}", headers=user_headers
        )
    ).json()
    assert [u["short_code"] for u in second["items"]] == ["alice-1", "alice-0"]
    assert second["next_cursor"] is None


async def test_other_users_link_is_not_found(client: AsyncClient, user_headers: Headers) -> None:
    await shorten(client, user_headers, custom_alias="private")
    mallory = await register_and_login(client, "mallory@example.com")

    assert (await client.get("/api/v1/urls/private", headers=mallory)).status_code == 404
    patch = await client.patch(
        "/api/v1/urls/private", json={"target_url": "https://evil.example"}, headers=mallory
    )
    assert patch.status_code == 404
    assert (await client.delete("/api/v1/urls/private", headers=mallory)).status_code == 404


async def test_update_link(client: AsyncClient, user_headers: Headers) -> None:
    await shorten(client, user_headers, custom_alias="editable")
    response = await client.patch(
        "/api/v1/urls/editable",
        json={"target_url": "https://example.org/new", "is_active": False},
        headers=user_headers,
    )
    assert response.status_code == 200
    assert response.json()["target_url"] == "https://example.org/new"
    assert (await client.get("/editable")).status_code == 404  # deactivated

    await client.patch("/api/v1/urls/editable", json={"is_active": True}, headers=user_headers)
    assert (await client.get("/editable")).headers["location"] == "https://example.org/new"


async def test_delete_is_soft_and_code_is_never_reused(
    client: AsyncClient, user_headers: Headers
) -> None:
    await shorten(client, user_headers, custom_alias="gone-soon")
    assert (await client.delete("/api/v1/urls/gone-soon", headers=user_headers)).status_code == 204
    assert (await client.get("/gone-soon")).status_code == 404

    response = await client.post(
        "/api/v1/urls",
        json={"target_url": "https://attacker.example", "custom_alias": "gone-soon"},
        headers=user_headers,
    )
    assert response.status_code == 409
