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
make up          # nginx on :8000 -> 2 API replicas; postgres :5432; redis :6379
curl localhost:8000/health/live
make down
```

Scale out with `docker compose up -d --scale api=4 --scale worker=2`.
Run `make help` to list the development targets.

Create an admin account with `uv run python -m app.cli create-admin you@example.com`.
