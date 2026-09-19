import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, UnauthorizedError
from app.core.security import (
    burn_password_check,
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.models import RefreshToken, Role, User
from app.schemas.auth import TokenResponse

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def register(self, email: str, password: str, role: Role = Role.USER) -> User:
        stmt = (
            insert(User)
            .values(
                email=email.lower(),
                password_hash=await hash_password(password),
                role=role.value,
            )
            .on_conflict_do_nothing(index_elements=[User.email])
            .returning(User)
        )
        user: User | None = await self._session.scalar(stmt)
        if user is None:
            raise ConflictError("an account with this email already exists")
        await self._session.commit()
        return user

    async def authenticate(self, email: str, password: str) -> User:
        user = await self._session.scalar(select(User).where(User.email == email.lower()))
        if user is None:
            await burn_password_check(password)
            raise UnauthorizedError("invalid email or password")
        if not await verify_password(user.password_hash, password) or not user.is_active:
            raise UnauthorizedError("invalid email or password")
        return user

    async def login(self, email: str, password: str) -> TokenResponse:
        user = await self.authenticate(email, password)
        # Every login starts a new token family, so each device/session can be
        # revoked on its own.
        tokens = await self._issue(user, family_id=uuid.uuid4())
        await self._session.commit()
        return tokens

    async def refresh(self, presented: str) -> TokenResponse:
        token_hash = hash_refresh_token(presented)
        # FOR UPDATE serializes concurrent refreshes of the same token: exactly one
        # wins the rotation; the other then sees a revoked token.
        record = await self._session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
        )
        if record is None:
            raise UnauthorizedError("invalid refresh token")

        now = datetime.now(UTC)
        if record.revoked_at is not None:
            await self._revoke_family(record.family_id, now)
            await self._session.commit()
            logger.warning(
                "refresh token reuse detected; family revoked",
                extra={"user_id": record.user_id, "family_id": str(record.family_id)},
            )
            raise UnauthorizedError("refresh token has been revoked")
        if record.expires_at <= now:
            raise UnauthorizedError("refresh token has expired")

        user = await self._session.get(User, record.user_id)
        if user is None or not user.is_active:
            raise UnauthorizedError("account is disabled or no longer exists")

        tokens = await self._issue(user, family_id=record.family_id)
        record.revoked_at = now
        record.replaced_by = hash_refresh_token(tokens.refresh_token)
        await self._session.commit()
        return tokens

    async def logout(self, presented: str) -> None:
        record = await self._session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(presented))
        )
        if record is not None:
            await self._revoke_family(record.family_id, datetime.now(UTC))
            await self._session.commit()

    async def get_user(self, user_id: int) -> User:
        user = await self._session.get(User, user_id)
        if user is None or not user.is_active:
            raise UnauthorizedError("account is disabled or no longer exists")
        return user

    async def set_role(self, user_id: int, role: Role) -> User:
        user = await self._session.get(User, user_id)
        if user is None:
            raise NotFoundError("user not found")
        user.role = role.value
        # Revoke refresh tokens so the next access token picks up the new role.
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.commit()
        return user

    async def _issue(self, user: User, family_id: uuid.UUID) -> TokenResponse:
        refresh_token, refresh_hash = new_refresh_token()
        ttl = self._settings.refresh_token_ttl_seconds
        self._session.add(
            RefreshToken(
                token_hash=refresh_hash,
                user_id=user.id,
                family_id=family_id,
                expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
            )
        )
        await self._session.flush()
        return TokenResponse(
            access_token=create_access_token(user.id, user.role, self._settings),
            expires_in=self._settings.access_token_ttl_seconds,
            refresh_token=refresh_token,
            refresh_expires_in=ttl,
        )

    async def _revoke_family(self, family_id: uuid.UUID, now: datetime) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
