from fastapi import APIRouter, Depends, Response, status

from app.api.deps import AuthServiceDep, PrincipalDep
from app.api.ratelimit import limit_by_ip, limit_by_user
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/api/v1", tags=["auth"])

# Credential endpoints are the brute-force target, so they are limited per client IP.
_auth_limit = [Depends(limit_by_ip("auth"))]


@router.post("/auth/register", status_code=status.HTTP_201_CREATED, dependencies=_auth_limit)
async def register(body: RegisterRequest, auth: AuthServiceDep) -> UserResponse:
    user = await auth.register(body.email, body.password)
    return UserResponse.model_validate(user)


@router.post("/auth/login", dependencies=_auth_limit)
async def login(body: LoginRequest, auth: AuthServiceDep) -> TokenResponse:
    return await auth.login(body.email, body.password)


@router.post("/auth/refresh", dependencies=_auth_limit)
async def refresh(body: RefreshRequest, auth: AuthServiceDep) -> TokenResponse:
    """Exchange a refresh token for a new token pair. The presented token is revoked."""
    return await auth.refresh(body.refresh_token)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, auth: AuthServiceDep) -> Response:
    await auth.logout(body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/users/me", dependencies=[Depends(limit_by_user("read"))])
async def me(principal: PrincipalDep, auth: AuthServiceDep) -> UserResponse:
    return UserResponse.model_validate(await auth.get_user(principal.user_id))
