from fastapi import APIRouter, status
from fastapi.responses import RedirectResponse

from app.api.deps import ResolverDep
from app.core.errors import GoneError, NotFoundError

router = APIRouter(tags=["redirect"])

MAX_CODE_LENGTH = 32


@router.get("/{short_code}", response_class=RedirectResponse)
async def follow(short_code: str, resolver: ResolverDep) -> RedirectResponse:
    """Redirect to the target URL.

    302 rather than 301: browsers cache 301s indefinitely, so later clicks would never
    reach us. That would hide them from analytics and stop edits or deactivation from
    taking effect.
    """
    if len(short_code) > MAX_CODE_LENGTH:
        raise NotFoundError("short link not found")
    link = await resolver.resolve(short_code)
    if link is None or not link.is_active:
        raise NotFoundError("short link not found")
    if link.is_expired():
        raise GoneError("short link has expired")
    return RedirectResponse(link.target_url, status_code=status.HTTP_302_FOUND)
