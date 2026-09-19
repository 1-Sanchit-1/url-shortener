import json
import logging

import pytest
import structlog
from httpx import AsyncClient

from app.core.logging import configure_logging


async def test_request_id_is_generated(client: AsyncClient) -> None:
    response = await client.get("/health/live")
    assert len(response.headers["x-request-id"]) == 32


async def test_request_id_is_propagated(client: AsyncClient) -> None:
    response = await client.get("/health/live", headers={"X-Request-ID": "trace-abc.123"})
    assert response.headers["x-request-id"] == "trace-abc.123"


async def test_malformed_request_id_is_replaced(client: AsyncClient) -> None:
    response = await client.get("/health/live", headers={"X-Request-ID": "bad id\nforged-line"})
    assert response.headers["x-request-id"] != "bad id\nforged-line"
    assert len(response.headers["x-request-id"]) == 32


async def test_metrics_use_route_templates(client: AsyncClient) -> None:
    await client.get("/some-code")
    await client.get("/another-code")
    body = (await client.get("/metrics")).text

    assert 'http_requests_total{method="GET",route="/{short_code}",status="404"}' in body
    assert "some-code" not in body
    assert "http_request_duration_seconds_bucket" in body
    assert 'link_cache_lookups_total{result="miss",tier="l1"}' in body


async def test_access_log_records_request_details(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app.access"):
        await client.get("/does-not-exist")
    record = next(r for r in caplog.records if r.name == "app.access")
    assert record.__dict__["route"] == "/{short_code}"
    assert record.__dict__["status"] == 404
    assert isinstance(record.__dict__["duration_ms"], float)


def test_logs_render_as_json_with_request_context(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "json")
    structlog.contextvars.bind_contextvars(request_id="req-42")
    try:
        logging.getLogger("app.test").warning("cache degraded", extra={"tier": "l2"})
    finally:
        structlog.contextvars.clear_contextvars()

    line = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert line["event"] == "cache degraded"
    assert line["level"] == "warning"
    assert line["request_id"] == "req-42"
    assert line["tier"] == "l2"
    assert line["logger"] == "app.test"
    assert "timestamp" in line
