"""Short code generation and custom alias validation.

Generated codes are uniformly random base62 strings rather than an encoded
auto-increment ID. Sequential IDs would never collide, but they leak creation
volume and make every link enumerable. A random 7-character code has
62**7 ≈ 3.5e12 possible values; at 100M stored links a new code collides with
probability ~3e-5, so the retry loop in the repository almost never runs more
than once.
"""

import re
import secrets
import string
from collections.abc import Callable

BASE62_ALPHABET = string.digits + string.ascii_uppercase + string.ascii_lowercase
_BASE = len(BASE62_ALPHABET)
_INDEX = {char: i for i, char in enumerate(BASE62_ALPHABET)}

ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
# Paths the service owns at the top level; an alias must never shadow them.
RESERVED_ALIASES = frozenset(
    {"api", "admin", "docs", "redoc", "openapi.json", "health", "metrics", "static", "favicon.ico"}
)

CodeGenerator = Callable[[int], str]


def encode_base62(value: int) -> str:
    if value < 0:
        raise ValueError("base62 encoding requires a non-negative integer")
    if value == 0:
        return BASE62_ALPHABET[0]
    chars: list[str] = []
    while value:
        value, remainder = divmod(value, _BASE)
        chars.append(BASE62_ALPHABET[remainder])
    return "".join(reversed(chars))


def decode_base62(encoded: str) -> int:
    value = 0
    for char in encoded:
        try:
            value = value * _BASE + _INDEX[char]
        except KeyError:
            raise ValueError(f"invalid base62 character: {char!r}") from None
    return value


def random_code(length: int) -> str:
    """Return a cryptographically random base62 code of exactly ``length`` characters."""
    # A single draw from [0, 62**length) is uniform; left-padding keeps the length fixed.
    return encode_base62(secrets.randbelow(_BASE**length)).rjust(length, BASE62_ALPHABET[0])


class InvalidAliasError(ValueError):
    pass


def validate_alias(alias: str) -> str:
    if not ALIAS_PATTERN.fullmatch(alias):
        raise InvalidAliasError("alias must be 3-32 characters of letters, digits, '-' or '_'")
    if alias.lower() in RESERVED_ALIASES:
        raise InvalidAliasError(f"alias '{alias}' is reserved")
    return alias
