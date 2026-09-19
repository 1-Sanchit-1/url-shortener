# Distributed URL Shortener

A URL shortener built with FastAPI, PostgreSQL and Redis. It focuses on low-latency
redirects, click analytics, and operating the service in production.

## Development

```bash
uv sync
uv run uvicorn --factory app.main:create_app --reload
uv run pytest
```
