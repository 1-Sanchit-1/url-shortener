.PHONY: help install up down logs db migrate worker lint format typecheck test check

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

install:  ## Install dependencies into .venv
	uv sync

up:  ## Start the full stack (nginx -> 2 API replicas, worker, postgres, redis)
	docker compose up --build -d

down:  ## Stop the stack
	docker compose down

logs:  ## Tail API logs
	docker compose logs -f api

db:  ## Start only PostgreSQL and Redis (for local development and tests)
	docker compose up -d postgres redis

migrate:  ## Apply database migrations
	uv run alembic upgrade head

worker:  ## Run the click-stream consumer locally
	uv run python -m app.workers.click_consumer

lint:  ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

format:  ## Auto-format and fix lint issues
	uv run ruff format .
	uv run ruff check --fix .

typecheck:  ## Run mypy in strict mode
	uv run mypy app tests

test:  ## Run the test suite
	uv run pytest

check: lint typecheck test  ## Run everything CI runs
