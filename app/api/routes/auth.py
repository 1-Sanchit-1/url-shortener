from fastapi import APIRouter, Response, status

from app.api.deps import AuthServiceDep, PrincipalDep
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/api/v1", tags=["auth"])


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, auth: AuthServiceDep) -> UserResponse:
    user = await auth.register(body.email, body.password)
    return UserResponse.model_validate(user)


@router.post("/auth/login")
async def login(body: LoginRequest, auth: AuthServiceDep) -> TokenResponse:
    return await auth.login(body.email, body.password)


@router.post("/auth/refresh")
async def refresh(body: RefreshRequest, auth: AuthServiceDep) -> TokenResponse:
    """Exchange a refresh token for a new token pair. The presented token is revoked."""
    return await auth.refresh(body.refresh_token)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, auth: AuthServiceDep) -> Response:
    await auth.logout(body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/users/me")
async def me(principal: PrincipalDep, auth: AuthServiceDep) -> UserResponse:
    return UserResponse.model_validate(await auth.get_user(principal.user_id))
