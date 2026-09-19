from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class Link:
    """The minimal data needed to serve a redirect; this is what gets cached."""

    url_id: int
    target_url: str
    is_active: bool
    expires_at: datetime | None

    def is_expired(self, now: datetime | None = None) -> bool:
        return self.expires_at is not None and self.expires_at <= (now or datetime.now(UTC))
