"""Create click_events table.

Revision ID: 0004_create_click_events
Revises: 0003_create_refresh_tokens
Create Date: 2026-01-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_create_click_events"
down_revision: str | None = "0003_create_refresh_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "click_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("event_id", sa.String(length=32), nullable=False),
        sa.Column("url_id", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("referrer_host", sa.String(length=255), nullable=True),
        sa.Column("user_agent", sa.String(length=256), nullable=True),
        sa.Column("visitor_hash", sa.String(length=16), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_click_events")),
        sa.UniqueConstraint("event_id", name=op.f("uq_click_events_event_id")),
    )
    op.create_index(op.f("ix_click_events_url_id"), "click_events", ["url_id"])


def downgrade() -> None:
    op.drop_table("click_events")
