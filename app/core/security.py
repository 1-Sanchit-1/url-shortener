import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.errors import UnauthorizedError
from app.models.user import Role

_hasher = PasswordHasher()  # argon2id with the library's RFC 9106 defaults
# Each argon2 hash allocates 64 MiB and pins a CPU core. Capping concurrent hashes
# stops a burst of logins from exhausting memory or starving the threadpool that
# other blocking work depends on.
_hash_slots = asyncio.Semaphore(4)
_dummy_hash: str | None = None


async def hash_password(password: str) -> str:
    async with _hash_slots:
        return await run_in_threadpool(_hasher.hash, password)


async def verify_password(password_hash: str, password: str) -> bool:
    async with _hash_slots:
        try:
            return await run_in_threadpool(_hasher.verify, password_hash, password)
        except (VerificationError, InvalidHashError):
            return False


async def burn_password_check(password: str) -> None:
    """Spend the same time as a real verification when the account doesn't exist.

    Without this, login latency reveals which email addresses are registered.
    """
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = await hash_password("dummy-password-for-timing")
    await verify_password(_dummy_hash, password)


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller, reconstructed from a verified access token."""

    user_id: int
    role: Role

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN


def create_access_token(user_id: int, role: str, settings: Settings) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + timedelta(seconds=settings.access_token_ttl_seconds),
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> Principal:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            # Pinning the algorithm list blocks "alg: none" and algorithm-confusion attacks.
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
        if claims.get("type") != "access":
            raise UnauthorizedError("wrong token type")
        return Principal(user_id=int(claims["sub"]), role=Role(claims["role"]))
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise UnauthorizedError("invalid or expired token") from exc
