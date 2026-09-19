# Benchmarks

Everything here can be reproduced from a clean checkout. The numbers in
[PERFORMANCE.md](PERFORMANCE.md) were produced with exactly these commands.

## Setup

```bash
make up                                   # nginx -> 2 API replicas, worker, postgres, redis
make migrate                              # (the compose "migrate" job also does this)
make seed                                 # 100k links, 5M clicks over 30 days (power-law skew)
```

`scripts/seed.py` generates rows inside PostgreSQL with `generate_series`. Link
popularity is skewed (`power(random(), 3)`), so the hottest link has ~107k clicks
and the median link has a few dozen. Analytics cost is dominated by hot links,
which is also the case that matters in production.

## Running a load test

```bash
make bench LABEL=baseline                 # or: USERS=300 DURATION=3m scripts/bench.sh baseline
```

`scripts/bench.sh`:

1. Starts the stack with `docker-compose.bench.yml`, which disables rate limiting.
   All traffic comes from one IP, so per-IP limits would reject most of it.
2. Resets `pg_stat_statements`.
3. Runs Locust **inside the compose network** (4 worker processes) against nginx.
   This keeps Docker Desktop's host port forwarding out of the measurement.
4. Saves to `loadtest/results/<timestamp>-<label>/`: Locust CSV + HTML report, the
   10 most expensive SQL statements, and the git revision and host details.
5. Prints a Markdown table ready to paste into PERFORMANCE.md.

### Load profile (`loadtest/locustfile.py`)

| Share | Request | Notes |
|---|---|---|
| ~95% | `GET /{code}` | Zipf(s=1.0) over 100k seeded codes, so a few are very hot |
| ~2% | `GET /{code}` unknown | exercises negative caching |
| ~3% | analytics + link creation | signed-in owner; analytics on the 1,000 hottest links |

Default: 200 users (~1,900 req/s offered), 50/s spawn rate, 2 minutes.

## Capturing query plans

```bash
make explain > docs/plans/<label>.txt
```

`scripts/explain.py` runs the real service code for the hottest link, records every
SQL statement it issues, and re-runs each under
`EXPLAIN (ANALYZE, BUFFERS, SETTINGS)`. Because the statements are captured rather
than copied by hand, the plans always match what the application runs.

## Caveats

- Locust, nginx, the API replicas, PostgreSQL and Redis all share one machine,
  and on macOS one Docker VM. Absolute numbers are lower than on dedicated
  hosts. Comparisons between runs on the same machine are meaningful.
- Run each configuration at least twice and compare medians. Discard the first
  run after `make seed`, while PostgreSQL's buffer cache is still cold.
