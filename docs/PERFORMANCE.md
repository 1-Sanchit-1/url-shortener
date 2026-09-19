# Performance: profiling and optimization log

Every number below comes from `scripts/bench.sh` and `scripts/explain.py` (see
[BENCHMARKS.md](BENCHMARKS.md)). Raw plans are in [`plans/`](plans/).

**Environment:** Apple M4 Pro, Docker Desktop VM with 12 CPUs / 8 GB, all services
plus the Locust load generator on one machine. Dataset: 100k links, 5M clicks
over 30 days (power-law skew; the hottest link has ~125k clicks). Load:
200 Locust users, ~1,930 req/s offered, 2-minute runs.

## Summary

| Stage | Redirect p50 / p95 / p99 | Analytics p50 / p95 / p99 | Notes |
|---|---|---|---|
| Baseline (warm) | 5 / 78 / **130 ms** | 25 / 190 / **280 ms** | 2 API replicas |
| + hourly rollups, covering index | 2 / 28 / **54 ms** | 5 / 58 / **120 ms** | second of two runs; first: 61 / 140 |
| + batched click publishing | 2 / 20 / **42 ms** | 7 / 41 / **69 ms** | API CPU 65% → ~47% |
| 2x load (3,635 redirects/s), 2 replicas | 5 / 120 / 170 ms | 16 / 270 / 340 ms | both replicas at 97% CPU |
| 2x load (3,729 redirects/s), 4 replicas | 2 / 28 / **73 ms** | 6 / 60 / 150 ms | horizontal scale-out |

Zero failed requests in every run. **The redirect path holds p99 < 100 ms at
~3,700 req/s with 4 replicas on a single laptop.**

## 1. Analytics: from heap scans to rollups and index-only scans

### Symptom

Under load, `pg_stat_statements` ranked the three analytics queries as the top
three statements by total time (10.4 s, 2.4 s and 2.0 s over one 2-minute run),
with individual executions up to 180 ms.

### Diagnosis (`plans/01-baseline.txt`)

For the hottest link, each of the three queries looked like this:

```
Bitmap Heap Scan on click_events  (actual time=10.4..106.5 rows=44520)
  Recheck Cond: (url_id = $3)
  Filter: (occurred_at >= $4 AND occurred_at < $5)
  Rows Removed by Filter: 81315
  Buffers: shared read=59386
  ->  Bitmap Index Scan on ix_click_events_url_id  (rows=125835)
Execution Time: 120.5 ms
```

- The `url_id` index matched **every click the link ever had** (125k). The date
  range was applied afterwards, discarding 81k rows (65%).
- A popular link's clicks are spread over the whole table, so fetching them
  touched **59,386 heap pages (~464 MB)** per query, three times per request.
  That is more than the default `shared_buffers` (128 MB), so the pages could
  never stay cached. They were read again on every call and evicted other hot data.

### Changes (migration `0005`)

1. **`click_rollups_hourly (url_id, bucket_start) → clicks`**, maintained in the
   *same statement* that inserts raw events (`app/analytics/ingest.py`). A CTE
   inserts events with `ON CONFLICT DO NOTHING RETURNING` and aggregates only
   the rows actually written, so redelivered stream entries never double count.
   Events and rollups commit atomically.
2. **Covering index `(url_id, occurred_at) INCLUDE (visitor_hash, referrer_host)`**
   replaces the single-column index. The time predicate is now an index range,
   and the unique-visitor and referrer queries become index-only scans.
3. **Aggressive insert-triggered autovacuum** on `click_events`. Index-only scans
   still visited the heap (13.6k heap fetches) for recently inserted pages until
   VACUUM marked them all-visible. The default trigger (20% of the table) is
   far too lazy for an append-only table, so it is lowered to 1% / 10k rows.
4. **BRIN index on `occurred_at`** for the retention job
   (`python -m app.cli purge-clicks --days N`). A few pages cover millions of
   append-ordered rows.

### Result (`plans/02-rollups.txt`)

| Query (hottest link, 30 days) | Before | After |
|---|---|---|
| Daily series | 120.5 ms, 59,386 pages | **0.27 ms**, 156 pages (rollups) |
| Unique visitors | 122.3 ms, 59,386 pages | **23.2 ms**, index-only, 0 heap fetches |
| Top referrers | ~110 ms, 59,386 pages | **9.2 ms**, index-only, 0 heap fetches |

End to end, analytics p99 went from 280 to 120–140 ms. Redirect p99 also dropped
from 130 to 54–61 ms, although redirects never run these queries. The analytics
scans had been competing with everything else for CPU and buffer cache in the
shared VM.

### Trade-off and the next bottleneck

After this change, the ingestion statement became the most expensive query
(7.2 s total per run, max 123 ms). Cost moved from the read path, which a user
waits on, to the write path, which a background worker waits on. That was the
point. But a viral link's current-hour rollup row is now a *hot row*: every
batch upserts it. Next steps if it becomes a problem: pre-aggregate in Redis
(`HINCRBY`) and flush once per interval, or shard the counter row.

Unique visitors (`count(DISTINCT)`) is now the slowest analytics query. At larger
scale, a per-link HyperLogLog (`PFADD`/`PFCOUNT` in Redis) would give ~1% error
in O(1).

## 2. Redirect hot path: batching click events

### Diagnosis

At the target load both API replicas ran at ~65% CPU, so the tail was dominated
by event-loop queueing, not by I/O. I measured per-request server CPU in-process
(`time.process_time()` across 4,000 cached redirects, httpx client overhead
subtracted) and toggled one component at a time:

| Configuration | CPU per redirect |
|---|---|
| Everything on | ~420 µs |
| Access log lines off | ~370 µs (−~55 µs) |
| Access logs off *and* no click `XADD` | ~210 µs (−~160 µs) |

The per-redirect `XADD` (redis-py command encoding, reply parsing and a Starlette
background task) was **the single biggest cost on the redirect path**, larger
than routing, the cache lookup and the response combined.

### Change

`ClickPublisher` (`app/analytics/events.py`): a redirect now only appends to an
in-memory deque. A background task flushes it every 50 ms as **one pipelined
round trip**. The buffer is bounded (dropped events are counted in
`click_events_published_total{result="dropped"}`), and graceful shutdown flushes
what is left.

**Accepted risk:** a hard crash loses up to 50 ms of clicks. Analytics are
best-effort; redirects are not.

### Result

Same load, same data: API CPU dropped from **~65% to ~47%** per replica. Redirect
p99 improved from 54–61 ms to 42 ms, and analytics p99 from 120–140 ms to 69 ms.

### Measured and not claimed

- **Access-log sampling** (`APP_ACCESS_LOG_SAMPLE_RATE`; errors and slow requests
  are always logged). Two runs at 10% sampling gave redirect p99 of 55 and 78 ms,
  no better than batching alone within this machine's run-to-run noise. It is
  kept in the compose config because it cuts log volume 10x, not for latency.
- **Selecting only the needed columns in the redirect lookup.** That query
  averages 0.03 ms, runs only on a cache miss, and is not a bottleneck, so it
  wasn't changed.

## 3. Scaling out

At 2x load (3,635 redirects/s) both replicas hit 97% CPU and p99 rose to 170 ms.
With 4 replicas at the same load, redirect p99 was 73 ms at ~62% CPU each. The API
is stateless apart from its L1 cache, which pub/sub keeps coherent, so throughput
scales with replicas until Redis or PostgreSQL become the limit. At this load
PostgreSQL ran at ~30% CPU and Redis under 10%.

## Lessons from the process

- **The first "regression" was a load-balancer bug.** After switching analytics
  to rollups, every endpoint got *slower*, including redirects that never touch
  those tables. `docker stats` showed one API replica at 100% CPU and the other
  idle. nginx had resolved the `api` hostname once at startup, and the bench
  script had recreated the replicas with new IPs. The fix was `resolve` in the
  upstream plus Docker's DNS resolver. Lesson: check utilization balance before
  trusting a comparison.
- **The original baseline was invalidated by that bug**, so I re-measured it
  (checked out the pre-optimization commit, downgraded the schema, reseeded)
  instead of comparing against a number taken under different conditions.
- **Discard the first run after seeding.** Cold PostgreSQL and Redis caches
  inflated the tail by ~10 ms at p99.
- **A test suite that drops schemas needs a guard.** An exported dev
  `APP_DATABASE_URL` once pointed the tests at the benchmark database and wiped
  it. The suite now refuses any database whose name doesn't end in `_test`.
- **Docker's default 64 MB `/dev/shm` breaks PostgreSQL parallel query and
  parallel VACUUM** on multi-million-row tables (`could not resize shared memory
  segment`). Compose sets `shm_size: 512mb`.
