"""Load profile for the URL shortener.

Traffic mix (by request volume):
  ~95%  GET /{code}      redirects, Zipf-distributed over seeded codes (hot keys)
  ~2%   GET /{code}      unknown codes (404s exercise negative caching)
  ~3%   authenticated API: analytics on popular links, link creation

Run via scripts/bench.sh, which executes Locust inside the compose network so
results measure the service, not Docker Desktop's port forwarding.
"""

import itertools
import os
import random
import uuid
from pathlib import Path

from locust import FastHttpUser, constant_throughput, task

CODES = (
    Path(os.environ.get("CODES_FILE", Path(__file__).with_name("codes.txt"))).read_text().split()
)
ZIPF_S = float(os.environ.get("ZIPF_S", "1.0"))
# Zipf over popularity rank: rank 1 is the hottest link.
_CUM_WEIGHTS = list(itertools.accumulate(1 / (rank**ZIPF_S) for rank in range(1, len(CODES) + 1)))
HOT_CODES = CODES[:1000]

OWNER_EMAIL = "loadtest@example.com"
OWNER_PASSWORD = "loadtest-password"  # noqa: S105


def zipf_code() -> str:
    return random.choices(CODES, cum_weights=_CUM_WEIGHTS)[0]  # noqa: S311


class Visitor(FastHttpUser):
    """Anonymous clicks on short links."""

    weight = 30
    wait_time = constant_throughput(float(os.environ.get("VISITOR_RPS", "10")))

    @task(48)
    def follow_link(self) -> None:
        self.client.get(f"/{zipf_code()}", name="/{code}", allow_redirects=False)

    @task(1)
    def unknown_link(self) -> None:
        with self.client.get(
            f"/zz{uuid.uuid4().hex[:8]}", name="/{code} [404]", catch_response=True
        ) as response:
            if response.status_code == 404:
                response.success()


class Owner(FastHttpUser):
    """A signed-in user checking dashboards and creating links."""

    weight = 1
    wait_time = constant_throughput(float(os.environ.get("OWNER_RPS", "5")))

    def on_start(self) -> None:
        response = self.client.post(
            "/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        self.headers = {"Authorization": f"Bearer {response.json()['access_token']}"}

    @task(8)
    def analytics(self) -> None:
        code = random.choice(HOT_CODES)  # noqa: S311
        self.client.get(
            f"/api/v1/urls/{code}/analytics?granularity=day",
            name="/api/v1/urls/{code}/analytics",
            headers=self.headers,
        )

    @task(2)
    def create_link(self) -> None:
        self.client.post(
            "/api/v1/urls",
            json={"target_url": f"https://example.com/new/{uuid.uuid4().hex}"},
            headers=self.headers,
        )
