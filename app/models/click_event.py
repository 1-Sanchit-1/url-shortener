from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Identity, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ClickEvent(Base):
    """One redirect, as ingested from the click stream.

    No foreign key to ``urls``: this is the highest-volume table, and an FK check
    on every insert, plus a cascading delete over millions of rows, costs more than
    it protects. URLs are soft-deleted, so ``url_id`` never dangles in practice.
    """

    __tablename__ = "click_events"
    __table_args__ = (
        # The Redis stream entry id. Makes ingestion idempotent: a redelivered batch
        # hits ON CONFLICT DO NOTHING instead of double counting.
        UniqueConstraint("event_id"),
        Index(None, "url_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(32))
    url_id: Mapped[int] = mapped_column(BigInteger)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    referrer_host: Mapped[str | None] = mapped_column(String(255))
    user_agent: Mapped[str | None] = mapped_column(String(256))
    # Keyed hash of the client IP: enough to count unique visitors, but not reversible
    # to the address.
    visitor_hash: Mapped[str | None] = mapped_column(String(16))
