"""Print a Markdown summary of one Locust run: python -m scripts.summarize <result-dir>."""

import csv
import sys
from pathlib import Path

COLUMNS = ["Request Count", "Failure Count", "Requests/s", "50%", "95%", "99%", "Max Response Time"]


def main() -> None:
    out = Path(sys.argv[1])
    with (out / "locust_stats.csv").open() as fh:
        rows = list(csv.DictReader(fh))

    print(f"\n### {out.name}\n")
    print((out / "environment.txt").read_text())
    print("| Endpoint | Requests | Failures | RPS | p50 ms | p95 ms | p99 ms | max ms |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for row in rows:
        name = row["Name"] if row["Name"] != "Aggregated" else "**All**"
        values = [row[c] for c in COLUMNS]
        values[2] = f"{float(values[2]):.0f}"
        values[6] = f"{float(values[6]):.0f}"
        print(f"| `{name}` | " + " | ".join(values) + " |")
    print(f"\nTop PostgreSQL statements: {out / 'pg_stat_statements.txt'}")


if __name__ == "__main__":
    main()
