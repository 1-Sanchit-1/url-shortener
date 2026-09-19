from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AdminDep, AuthServiceDep, UrlServiceDep
from app.models import Role
from app.schemas.auth import RoleUpdateRequest, UserResponse
from app.schemas.urls import UrlPage

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/urls")
async def list_all_urls(
    _: AdminDep,
    service: UrlServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[int | None, Query(description="id of the last item seen")] = None,
) -> UrlPage:
    return await service.list_page(owner_id=None, limit=limit, before_id=cursor)


@router.put("/users/{user_id}/role")
async def set_user_role(
    user_id: int, body: RoleUpdateRequest, _: AdminDep, auth: AuthServiceDep
) -> UserResponse:
    return UserResponse.model_validate(await auth.set_role(user_id, Role(body.role)))
