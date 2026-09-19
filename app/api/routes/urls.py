from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import PrincipalDep, UrlServiceDep
from app.schemas.urls import ShortenRequest, UpdateUrlRequest, UrlPage, UrlResponse

router = APIRouter(prefix="/api/v1/urls", tags=["urls"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def shorten_url(
    body: ShortenRequest, principal: PrincipalDep, service: UrlServiceDep
) -> UrlResponse:
    url = await service.shorten(body, owner_id=principal.user_id)
    return service.to_response(url)


@router.get("")
async def list_my_urls(
    principal: PrincipalDep,
    service: UrlServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[int | None, Query(description="id of the last item seen")] = None,
) -> UrlPage:
    return await service.list_page(owner_id=principal.user_id, limit=limit, before_id=cursor)


@router.get("/{short_code}")
async def get_url(short_code: str, principal: PrincipalDep, service: UrlServiceDep) -> UrlResponse:
    return service.to_response(await service.get_for(short_code, principal))


@router.patch("/{short_code}")
async def update_url(
    short_code: str, body: UpdateUrlRequest, principal: PrincipalDep, service: UrlServiceDep
) -> UrlResponse:
    return service.to_response(await service.update(short_code, body, principal))


@router.delete("/{short_code}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_url(short_code: str, principal: PrincipalDep, service: UrlServiceDep) -> Response:
    await service.delete(short_code, principal)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
