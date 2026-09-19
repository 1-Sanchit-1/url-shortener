# Distributed URL Shortener

![CI](https://github.com/1-Sanchit-1/url-shortener/actions/workflows/ci.yml/badge.svg)

A URL shortener built with FastAPI, PostgreSQL and Redis. It focuses on low-latency
redirects, click analytics, and operating the service in production.

## Development

```bash
uv sync
make db          # PostgreSQL + Redis in Docker
make migrate
uv run uvicorn --factory app.main:create_app --reload
make test        # tests run against the shortener_test database
```

## Running with Docker

```bash
make up          # api on :8000, postgres on :5432, redis on :6379
curl localhost:8000/health/live
make down
```

Run `make help` to list the development targets.
