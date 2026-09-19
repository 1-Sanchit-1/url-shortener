"""Create urls table.

Revision ID: 0001_create_urls
Revises:
Create Date: 2026-01-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_create_urls"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "urls",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("short_code", sa.String(length=32, collation="C"), nullable=False),
        sa.Column("target_url", sa.Text(), nullable=False),
        sa.Column("is_custom", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "char_length(target_url) <= 2048", name=op.f("ck_urls_target_url_length")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_urls")),
        sa.UniqueConstraint("short_code", name=op.f("uq_urls_short_code")),
    )


def downgrade() -> None:
    op.drop_table("urls")
