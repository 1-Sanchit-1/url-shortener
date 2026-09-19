from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

MAX_TARGET_URL_LENGTH = 2048


class Url(Base):
    __tablename__ = "urls"
    __table_args__ = (
        # The unique constraint's btree index is the lookup path for every redirect.
        UniqueConstraint("short_code"),
        # Serves "list my links, newest first" with keyset pagination on id.
        Index(None, "owner_id", "id"),
        CheckConstraint(
            f"char_length(target_url) <= {MAX_TARGET_URL_LENGTH}", name="target_url_length"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    # COLLATE "C" gives byte-wise, case-sensitive comparison: correct for base62 codes
    # and cheaper than locale-aware collation on every index probe.
    short_code: Mapped[str] = mapped_column(String(32, collation="C"))
    target_url: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )
    is_custom: Mapped[bool] = mapped_column(Boolean, server_default=false())
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Soft delete: a deleted code is never reissued, so old links can't be hijacked.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
