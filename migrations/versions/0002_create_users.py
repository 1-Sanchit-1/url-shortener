"""Create users table and link URLs to their owners.

Revision ID: 0002_create_users
Revises: 0001_create_urls
Create Date: 2026-01-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_create_users"
down_revision: str | None = "0001_create_urls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=16), server_default=sa.text("'user'"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("role IN ('user', 'admin')", name=op.f("ck_users_role_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    # Nullable so rows created before authentication existed remain valid.
    op.add_column("urls", sa.Column("owner_id", sa.BigInteger(), nullable=True))
    op.add_column("urls", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        op.f("fk_urls_owner_id_users"), "urls", "users", ["owner_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index(op.f("ix_urls_owner_id_id"), "urls", ["owner_id", "id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_urls_owner_id_id"), table_name="urls")
    op.drop_constraint(op.f("fk_urls_owner_id_users"), "urls", type_="foreignkey")
    op.drop_column("urls", "deleted_at")
    op.drop_column("urls", "owner_id")
    op.drop_table("users")
