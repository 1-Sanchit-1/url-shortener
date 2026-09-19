import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ServiceUnavailableError
from app.models import Url
from app.services.shortcode import CodeGenerator, random_code

logger = logging.getLogger(__name__)


class ShortCodeExhaustedError(ServiceUnavailableError):
    """No free short code was found within the configured number of attempts."""

    code = "short_code_exhausted"


class AliasTakenError(ConflictError):
    code = "alias_taken"


async def try_insert(
    session: AsyncSession,
    *,
    short_code: str,
    target_url: str,
    is_custom: bool,
    expires_at: datetime | None,
    owner_id: int | None,
) -> Url | None:
    """Insert a URL, returning ``None`` instead of raising if the short code exists.

    ``ON CONFLICT DO NOTHING`` resolves the race between concurrent writers in the
    database itself, in one round trip. Catching ``IntegrityError`` instead would
    abort the surrounding transaction and need a SAVEPOINT for every attempt.
    """
    stmt = (
        insert(Url)
        .values(
            short_code=short_code,
            target_url=target_url,
            is_custom=is_custom,
            expires_at=expires_at,
            owner_id=owner_id,
        )
        .on_conflict_do_nothing(index_elements=[Url.short_code])
        .returning(Url)
    )
    url: Url | None = await session.scalar(stmt)
    return url


async def insert_with_generated_code(
    session: AsyncSession,
    *,
    target_url: str,
    expires_at: datetime | None,
    length: int,
    max_attempts: int,
    owner_id: int | None = None,
    generate: CodeGenerator = random_code,
) -> Url:
    for attempt in range(max_attempts):
        # The final attempt uses one extra character: 62x more keyspace, so a nearly
        # full keyspace degrades to slightly longer codes instead of failing requests.
        code_length = length if attempt < max_attempts - 1 else length + 1
        url = await try_insert(
            session,
            short_code=generate(code_length),
            target_url=target_url,
            is_custom=False,
            expires_at=expires_at,
            owner_id=owner_id,
        )
        if url is not None:
            return url
        logger.warning("short code collision", extra={"attempt": attempt + 1})
    raise ShortCodeExhaustedError(f"no free short code after {max_attempts} attempts")


async def insert_with_alias(
    session: AsyncSession,
    *,
    alias: str,
    target_url: str,
    expires_at: datetime | None,
    owner_id: int | None = None,
) -> Url:
    url = await try_insert(
        session,
        short_code=alias,
        target_url=target_url,
        is_custom=True,
        expires_at=expires_at,
        owner_id=owner_id,
    )
    if url is None:
        raise AliasTakenError(f"alias '{alias}' is already in use")
    return url


async def get_by_code(session: AsyncSession, short_code: str) -> Url | None:
    url: Url | None = await session.scalar(
        select(Url).where(Url.short_code == short_code, Url.deleted_at.is_(None))
    )
    return url


async def list_page(
    session: AsyncSession, *, owner_id: int | None, limit: int, before_id: int | None
) -> list[Url]:
    """Keyset pagination, newest first.

    ``WHERE id < :cursor ORDER BY id DESC LIMIT n`` costs the same on page 1000 as on
    page 1. OFFSET pagination would scan and discard every earlier row. With an owner
    filter this is a single range scan of ix_urls_owner_id_id.
    """
    stmt = select(Url).where(Url.deleted_at.is_(None)).order_by(Url.id.desc()).limit(limit)
    if owner_id is not None:
        stmt = stmt.where(Url.owner_id == owner_id)
    if before_id is not None:
        stmt = stmt.where(Url.id < before_id)
    return list(await session.scalars(stmt))
