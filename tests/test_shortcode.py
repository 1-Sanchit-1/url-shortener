import pytest

from app.services.shortcode import (
    BASE62_ALPHABET,
    InvalidAliasError,
    decode_base62,
    encode_base62,
    random_code,
    validate_alias,
)


@pytest.mark.parametrize("value", [0, 1, 61, 62, 3843, 3844, 2**63 - 1])
def test_base62_round_trip(value: int) -> None:
    assert decode_base62(encode_base62(value)) == value


def test_base62_known_values() -> None:
    assert encode_base62(0) == "0"
    assert encode_base62(61) == "z"
    assert encode_base62(62) == "10"


def test_encode_rejects_negative() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        encode_base62(-1)


def test_decode_rejects_invalid_character() -> None:
    with pytest.raises(ValueError, match="invalid base62"):
        decode_base62("ab$")


@pytest.mark.parametrize("length", [1, 7, 12])
def test_random_code_has_fixed_length_and_alphabet(length: int) -> None:
    for _ in range(200):
        code = random_code(length)
        assert len(code) == length
        assert set(code) <= set(BASE62_ALPHABET)


def test_random_codes_are_distinct() -> None:
    codes = {random_code(7) for _ in range(10_000)}
    assert len(codes) == 10_000


@pytest.mark.parametrize("alias", ["abc", "my-link", "Launch_2026", "a" * 32])
def test_valid_aliases(alias: str) -> None:
    assert validate_alias(alias) == alias


@pytest.mark.parametrize("alias", ["ab", "a" * 33, "has space", "emoji🙂", "slash/es", "a.b"])
def test_invalid_alias_format(alias: str) -> None:
    with pytest.raises(InvalidAliasError):
        validate_alias(alias)


@pytest.mark.parametrize("alias", ["api", "Health", "METRICS", "docs"])
def test_reserved_aliases_rejected(alias: str) -> None:
    with pytest.raises(InvalidAliasError, match="reserved"):
        validate_alias(alias)
