from httpx import AsyncClient

DEFAULT_PASSWORD = "correct-horse-battery-staple"


async def register_and_login(
    client: AsyncClient, email: str, password: str = DEFAULT_PASSWORD
) -> dict[str, str]:
    """Create an account and return an Authorization header for it."""
    response = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 201, response.text
    return await login(client, email, password)


async def login(
    client: AsyncClient, email: str, password: str = DEFAULT_PASSWORD
) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
