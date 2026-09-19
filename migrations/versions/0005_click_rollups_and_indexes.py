"""Add hourly click rollups and a covering index for analytics.

Revision ID: 0005_click_rollups_and_indexes
Revises: 0004_create_click_events
Create Date: 2026-01-05

Profiling under load showed analytics for popular links reading ~56k heap pages
per query. The single-column url_id index matched every click the link ever had,
and the time filter ran only after fetching each row from the heap. See
docs/PERFORMANCE.md.

Indexes are built CONCURRENTLY so the migration doesn't block ingestion on a
live table. That can't run inside a transaction, hence the autocommit blocks.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_click_rollups_and_indexes"
down_revision: str | None = "0004_create_click_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "click_rollups_hourly",
        sa.Column("url_id", sa.BigInteger(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("clicks", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("url_id", "bucket_start", name=op.f("pk_click_rollups_hourly")),
    )
    # Backfill from existing raw events. On a very large table, run this in
    # time-sliced batches before deploying the new consumer.
    op.execute(
        """
        INSERT INTO click_rollups_hourly (url_id, bucket_start, clicks)
        SELECT url_id, date_trunc('hour', occurred_at, 'UTC'), count(*)
        FROM click_events
        GROUP BY 1, 2
        """
    )
    # Index-only scans skip the heap only for pages marked all-visible, and only
    # VACUUM sets that bit. The default insert trigger (20% of the table) means
    # tens of thousands of recent, unvacuumed pages on a large append-only table, and
    # the analytics queries fall back to heap fetches for all of them.
    op.execute(
        "ALTER TABLE click_events SET ("
        "autovacuum_vacuum_insert_scale_factor = 0.01, "
        "autovacuum_vacuum_insert_threshold = 10000)"
    )
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_click_events_url_id_occurred_at",
            "click_events",
            ["url_id", "occurred_at"],
            postgresql_include=["visitor_hash", "referrer_host"],
            postgresql_concurrently=True,
        )
        op.create_index(
            "ix_click_events_occurred_at_brin",
            "click_events",
            ["occurred_at"],
            postgresql_using="brin",
            postgresql_concurrently=True,
        )
        # Redundant now: the composite index serves url_id-only lookups too.
        op.drop_index("ix_click_events_url_id", "click_events", postgresql_concurrently=True)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE click_events RESET ("
        "autovacuum_vacuum_insert_scale_factor, autovacuum_vacuum_insert_threshold)"
    )
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_click_events_url_id", "click_events", ["url_id"], postgresql_concurrently=True
        )
        op.drop_index(
            "ix_click_events_occurred_at_brin", "click_events", postgresql_concurrently=True
        )
        op.drop_index(
            "ix_click_events_url_id_occurred_at", "click_events", postgresql_concurrently=True
        )
    op.drop_table("click_rollups_hourly")
