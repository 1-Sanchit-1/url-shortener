from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, UnauthorizedError
from app.core.security import (
    burn_password_check,
    create_access_token,
    hash_password,
    verify_password,
)
from app.models import Role, User
from app.schemas.auth import TokenResponse


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

    def issue_tokens(self, user: User) -> TokenResponse:
        return TokenResponse(
            access_token=create_access_token(user.id, user.role, self._settings),
            expires_in=self._settings.access_token_ttl_seconds,
        )

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
        await self._session.commit()
        return user
