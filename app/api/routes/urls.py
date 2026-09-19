from fastapi import APIRouter, status

from app.api.deps import UrlServiceDep
from app.schemas.urls import ShortenRequest, UrlResponse

router = APIRouter(prefix="/api/v1/urls", tags=["urls"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def shorten_url(body: ShortenRequest, service: UrlServiceDep) -> UrlResponse:
    url = await service.shorten(body)
    return service.to_response(url)
