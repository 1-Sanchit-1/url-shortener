import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.urls import (
    AliasTakenError,
    ShortCodeExhaustedError,
    insert_with_alias,
    insert_with_generated_code,
)
from app.services.shortcode import CodeGenerator


def scripted(codes: list[str]) -> tuple[list[int], CodeGenerator]:
    """Build a generator that returns predetermined codes and records requested lengths."""
    lengths: list[int] = []
    it = iter(codes)

    def generate(length: int) -> str:
        lengths.append(length)
        return next(it)

    return lengths, generate


async def test_generated_code_retries_on_collision(session: AsyncSession) -> None:
    await insert_with_alias(
        session, alias="taken01", target_url="https://a.example", expires_at=None
    )

    lengths, generate = scripted(["taken01", "fresh01"])
    url = await insert_with_generated_code(
        session,
        target_url="https://b.example",
        expires_at=None,
        length=7,
        max_attempts=5,
        generate=generate,
    )
    await session.commit()

    assert url.short_code == "fresh01"
    assert url.is_custom is False
    assert lengths == [7, 7]


async def test_generated_code_gives_up_after_max_attempts(session: AsyncSession) -> None:
    await insert_with_alias(session, alias="dup", target_url="https://a.example", expires_at=None)

    lengths, generate = scripted(["dup"] * 3)
    with pytest.raises(ShortCodeExhaustedError):
        await insert_with_generated_code(
            session,
            target_url="https://b.example",
            expires_at=None,
            length=7,
            max_attempts=3,
            generate=generate,
        )
    # The last attempt widens the keyspace by one character.
    assert lengths == [7, 7, 8]


async def test_duplicate_alias_rejected(session: AsyncSession) -> None:
    await insert_with_alias(session, alias="promo", target_url="https://a.example", expires_at=None)
    with pytest.raises(AliasTakenError):
        await insert_with_alias(
            session, alias="promo", target_url="https://b.example", expires_at=None
        )


async def test_short_codes_are_case_sensitive(session: AsyncSession) -> None:
    first = await insert_with_alias(
        session, alias="Promo", target_url="https://a.example", expires_at=None
    )
    second = await insert_with_alias(
        session, alias="promo", target_url="https://b.example", expires_at=None
    )
    assert first.id != second.id
