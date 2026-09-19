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
        # Covering index for the per-link analytics queries (unique visitors and
        # top referrers over a time range). The time predicate is applied inside the
        # index, and INCLUDE lets both queries run as index-only scans without
        # touching the heap. A popular link's rows are spread across the whole
        # table, so heap access was the dominant cost.
        Index(
            "ix_click_events_url_id_occurred_at",
            "url_id",
            "occurred_at",
            postgresql_include=["visitor_hash", "referrer_host"],
        ),
        # Rows arrive in time order, so a BRIN index (a few pages for millions of
        # rows) is enough for retention jobs that scan by time alone.
        Index("ix_click_events_occurred_at_brin", "occurred_at", postgresql_using="brin"),
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


class ClickRollupHourly(Base):
    """Pre-aggregated click counts per link per UTC hour.

    Maintained in the same statement that inserts raw events (see
    ``app.analytics.ingest``), so it can never drift from the raw data. A 30-day
    daily series reads at most 720 rows here instead of every raw event.
    """

    __tablename__ = "click_rollups_hourly"

    url_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    clicks: Mapped[int] = mapped_column(BigInteger)
