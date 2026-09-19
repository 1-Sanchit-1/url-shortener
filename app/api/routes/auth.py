from fastapi import APIRouter, status

from app.api.deps import AuthServiceDep, PrincipalDep
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/api/v1", tags=["auth"])


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, auth: AuthServiceDep) -> UserResponse:
    user = await auth.register(body.email, body.password)
    return UserResponse.model_validate(user)


@router.post("/auth/login")
async def login(body: LoginRequest, auth: AuthServiceDep) -> TokenResponse:
    user = await auth.authenticate(body.email, body.password)
    return auth.issue_tokens(user)


@router.get("/users/me")
async def me(principal: PrincipalDep, auth: AuthServiceDep) -> UserResponse:
    return UserResponse.model_validate(await auth.get_user(principal.user_id))
