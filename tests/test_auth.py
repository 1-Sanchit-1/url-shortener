from datetime import UTC, datetime, timedelta

import jwt
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.config import Settings
from tests.utils import DEFAULT_PASSWORD, register_and_login

Headers = dict[str, str]


async def test_register_and_fetch_profile(client: AsyncClient) -> None:
    headers = await register_and_login(client, "Carol@Example.com")
    me = await client.get("/api/v1/users/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == "carol@example.com"
    assert me.json()["role"] == "user"
    assert "password_hash" not in me.json()


async def test_duplicate_email_is_case_insensitive(client: AsyncClient) -> None:
    await register_and_login(client, "dave@example.com")
    response = await client.post(
        "/api/v1/auth/register", json={"email": "DAVE@example.com", "password": DEFAULT_PASSWORD}
    )
    assert response.status_code == 409


async def test_weak_password_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register", json={"email": "erin@example.com", "password": "short"}
    )
    assert response.status_code == 422


async def test_login_failures_are_indistinguishable(client: AsyncClient) -> None:
    await register_and_login(client, "frank@example.com")
    wrong_password = await client.post(
        "/api/v1/auth/login", json={"email": "frank@example.com", "password": "wrong-password!"}
    )
    unknown_user = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong-password!"}
    )
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()


async def test_missing_token_is_401(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/users/me")).status_code == 401


def _forge(settings: Settings, **overrides: object) -> Headers:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": "1",
        "role": "admin",
        "type": "access",
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    token = jwt.encode(claims, settings.jwt_secret.get_secret_value(), "HS256")
    return {"Authorization": f"Bearer {token}"}


async def test_expired_token_rejected(client: AsyncClient, settings: Settings) -> None:
    headers = _forge(settings, exp=datetime.now(UTC) - timedelta(seconds=1))
    assert (await client.get("/api/v1/admin/urls", headers=headers)).status_code == 401


async def test_token_signed_with_other_key_rejected(client: AsyncClient) -> None:
    other = Settings(jwt_secret=SecretStr("a-completely-different-secret-value"))
    assert (await client.get("/api/v1/admin/urls", headers=_forge(other))).status_code == 401


async def test_unsigned_token_rejected(client: AsyncClient, settings: Settings) -> None:
    claims = {"sub": "1", "role": "admin", "type": "access", "iss": settings.jwt_issuer}
    token = jwt.encode(claims, key="", algorithm="none")
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/v1/admin/urls", headers=headers)).status_code == 401


async def test_wrong_token_type_rejected(client: AsyncClient, settings: Settings) -> None:
    headers = _forge(settings, type="refresh")
    assert (await client.get("/api/v1/admin/urls", headers=headers)).status_code == 401


async def test_user_cannot_reach_admin_routes(client: AsyncClient, user_headers: Headers) -> None:
    response = await client.get("/api/v1/admin/urls", headers=user_headers)
    assert response.status_code == 403


async def test_admin_sees_all_links_and_can_manage_them(
    client: AsyncClient, user_headers: Headers, admin_headers: Headers
) -> None:
    await client.post(
        "/api/v1/urls",
        json={"target_url": "https://example.com", "custom_alias": "alices"},
        headers=user_headers,
    )
    listing = await client.get("/api/v1/admin/urls", headers=admin_headers)
    assert [u["short_code"] for u in listing.json()["items"]] == ["alices"]

    deactivate = await client.patch(
        "/api/v1/urls/alices", json={"is_active": False}, headers=admin_headers
    )
    assert deactivate.status_code == 200


async def test_admin_can_promote_user(
    client: AsyncClient, user_headers: Headers, admin_headers: Headers
) -> None:
    me = (await client.get("/api/v1/users/me", headers=user_headers)).json()
    response = await client.put(
        f"/api/v1/admin/users/{me['id']}/role", json={"role": "admin"}, headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"
