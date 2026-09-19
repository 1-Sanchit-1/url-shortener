import asyncio

from httpx import AsyncClient

from tests.utils import DEFAULT_PASSWORD


async def _login(client: AsyncClient, email: str = "gina@example.com") -> dict[str, str]:
    await client.post("/api/v1/auth/register", json={"email": email, "password": DEFAULT_PASSWORD})
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
    )
    tokens: dict[str, str] = response.json()
    return tokens


async def _refresh(client: AsyncClient, token: str) -> tuple[int, dict[str, str]]:
    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": token})
    return response.status_code, response.json()


async def test_refresh_rotates_tokens(client: AsyncClient) -> None:
    tokens = await _login(client)
    status, rotated = await _refresh(client, tokens["refresh_token"])
    assert status == 200
    assert rotated["refresh_token"] != tokens["refresh_token"]

    me = await client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {rotated['access_token']}"}
    )
    assert me.status_code == 200


async def test_reusing_rotated_token_revokes_family(client: AsyncClient) -> None:
    tokens = await _login(client)
    _, rotated = await _refresh(client, tokens["refresh_token"])

    # An attacker replays the stolen, already-rotated token...
    status, _ = await _refresh(client, tokens["refresh_token"])
    assert status == 401
    # ...which also invalidates the legitimate client's current token.
    status, _ = await _refresh(client, rotated["refresh_token"])
    assert status == 401


async def test_families_are_independent(client: AsyncClient) -> None:
    laptop = await _login(client)
    response = await client.post(
        "/api/v1/auth/login", json={"email": "gina@example.com", "password": DEFAULT_PASSWORD}
    )
    phone = response.json()

    await _refresh(client, laptop["refresh_token"])
    await _refresh(client, laptop["refresh_token"])  # reuse -> laptop family revoked

    status, _ = await _refresh(client, phone["refresh_token"])
    assert status == 200


async def test_concurrent_refresh_has_single_winner(client: AsyncClient) -> None:
    tokens = await _login(client)
    results = await asyncio.gather(
        _refresh(client, tokens["refresh_token"]), _refresh(client, tokens["refresh_token"])
    )
    assert sorted(status for status, _ in results) == [200, 401]


async def test_logout_revokes_refresh_token(client: AsyncClient) -> None:
    tokens = await _login(client)
    response = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 204
    status, _ = await _refresh(client, tokens["refresh_token"])
    assert status == 401


async def test_unknown_refresh_token_rejected(client: AsyncClient) -> None:
    status, _ = await _refresh(client, "not-a-real-token")
    assert status == 401


async def test_role_change_revokes_sessions(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    tokens = await _login(client, "henry@example.com")
    me = await client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    await client.put(
        f"/api/v1/admin/users/{me.json()['id']}/role", json={"role": "admin"}, headers=admin_headers
    )
    status, _ = await _refresh(client, tokens["refresh_token"])
    assert status == 401
