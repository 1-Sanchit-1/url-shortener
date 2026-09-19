from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Annotated, Any, cast

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.container import Container
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import Principal, decode_access_token
from app.models import Role
from app.services.auth import AuthService
from app.services.links import LinkResolver
from app.services.urls import UrlService


def get_container(request: Request) -> Container:
    return cast(Container, request.app.state.container)


ContainerDep = Annotated[Container, Depends(get_container)]


def get_app_settings(container: ContainerDep) -> Settings:
    return container.settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


async def get_session(container: ContainerDep) -> AsyncIterator[AsyncSession]:
    async with container.sessionmaker() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]

_bearer = HTTPBearer(auto_error=False)


async def get_principal(
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    """Authenticate from the JWT alone, with no database round trip.

    Trade-off: a role change or account disable takes effect when the access token
    expires (15 minutes by default), not immediately. Short token lifetimes plus
    refresh-token revocation bound that window.
    """
    if credentials is None:
        raise UnauthorizedError("missing bearer token")
    return decode_access_token(credentials.credentials, settings)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def require_role(*roles: Role) -> Callable[[Principal], Coroutine[Any, Any, Principal]]:
    async def dependency(principal: PrincipalDep) -> Principal:
        if principal.role not in roles:
            raise ForbiddenError("insufficient role for this operation")
        return principal

    return dependency


AdminDep = Annotated[Principal, Depends(require_role(Role.ADMIN))]


def get_url_service(session: SessionDep, container: ContainerDep) -> UrlService:
    return UrlService(session, container.settings, container.resolver)


UrlServiceDep = Annotated[UrlService, Depends(get_url_service)]


def get_auth_service(session: SessionDep, settings: SettingsDep) -> AuthService:
    return AuthService(session, settings)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def get_resolver(container: ContainerDep) -> LinkResolver:
    return container.resolver


ResolverDep = Annotated[LinkResolver, Depends(get_resolver)]
