from httpx import AsyncClient


async def test_liveness(client: AsyncClient) -> None:
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_reports_dependencies(client: AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["checks"] == {"postgres": "ok", "redis": "ok"}
