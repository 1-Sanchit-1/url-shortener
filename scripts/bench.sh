#!/usr/bin/env bash
# Reproducible load test. Usage:
#   scripts/bench.sh <label>                         # defaults below
#   USERS=300 DURATION=3m scripts/bench.sh after-rollups
#
# Writes loadtest/results/<timestamp>-<label>/ with Locust CSV/HTML reports,
# the top PostgreSQL statements (pg_stat_statements), and the git revision.
set -euo pipefail

LABEL="${1:-run}"
USERS="${USERS:-200}"
SPAWN_RATE="${SPAWN_RATE:-50}"
DURATION="${DURATION:-2m}"
PROCESSES="${PROCESSES:-4}"
OUT="loadtest/results/$(date +%Y%m%d-%H%M%S)-${LABEL}"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.bench.yml)
PSQL=("${COMPOSE[@]}" exec -T postgres psql -U shortener -d shortener -q)

mkdir -p "$OUT"
"${COMPOSE[@]}" up -d --build --wait nginx api worker >/dev/null

"${PSQL[@]}" -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements" \
             -c "SELECT pg_stat_statements_reset()" >/dev/null

{
  echo "commit: $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "users=$USERS spawn_rate=$SPAWN_RATE duration=$DURATION api_replicas=${API_REPLICAS:-2}"
  echo "host: $(uname -sm), docker cpus=$(docker info --format '{{.NCPU}}') mem=$(docker info --format '{{.MemTotal}}')"
} > "$OUT/environment.txt"

"${COMPOSE[@]}" --profile bench run --rm locust \
  -f loadtest/locustfile.py --headless \
  --host http://nginx \
  -u "$USERS" -r "$SPAWN_RATE" -t "$DURATION" \
  --processes "$PROCESSES" \
  --csv "$OUT/locust" --html "$OUT/report.html" \
  --only-summary >/dev/null 2>"$OUT/locust.log" || true

"${PSQL[@]}" -P pager=off -c "
  SELECT calls,
         round(total_exec_time::numeric, 1) AS total_ms,
         round(mean_exec_time::numeric, 3)  AS mean_ms,
         round(max_exec_time::numeric, 1)   AS max_ms,
         shared_blks_hit + shared_blks_read AS blocks,
         left(regexp_replace(query, '\s+', ' ', 'g'), 160) AS query
  FROM pg_stat_statements
  WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
  ORDER BY total_exec_time DESC
  LIMIT 10" > "$OUT/pg_stat_statements.txt"

uv run python -m scripts.summarize "$OUT"
