from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import InvalidRequestError
from app.models import Url
from app.models.url import MAX_TARGET_URL_LENGTH
from app.repositories import urls as url_repo
from app.schemas.urls import ShortenRequest, UrlResponse
from app.services.shortcode import validate_alias


@dataclass(frozen=True, slots=True)
class Link:
    """The minimal data needed to serve a redirect."""

    url_id: int
    target_url: str
    is_active: bool
    expires_at: datetime | None

    def is_expired(self, now: datetime | None = None) -> bool:
        return self.expires_at is not None and self.expires_at <= (now or datetime.now(UTC))


class UrlService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def shorten(self, request: ShortenRequest) -> Url:
        target = request.target()
        self._validate_target(target)
        if request.expires_at is not None and request.expires_at <= datetime.now(UTC):
            raise InvalidRequestError("expires_at must be in the future")

        if request.custom_alias is not None:
            url = await url_repo.insert_with_alias(
                self._session,
                alias=validate_alias(request.custom_alias),
                target_url=target,
                expires_at=request.expires_at,
            )
        else:
            url = await url_repo.insert_with_generated_code(
                self._session,
                target_url=target,
                expires_at=request.expires_at,
                length=self._settings.short_code_length,
                max_attempts=self._settings.short_code_max_attempts,
            )
        await self._session.commit()
        return url

    async def resolve(self, short_code: str) -> Link | None:
        url = await self._session.scalar(select(Url).where(Url.short_code == short_code))
        if url is None:
            return None
        return Link(
            url_id=url.id,
            target_url=url.target_url,
            is_active=url.is_active,
            expires_at=url.expires_at,
        )

    def to_response(self, url: Url) -> UrlResponse:
        return UrlResponse(
            short_code=url.short_code,
            short_url=f"{self._settings.base_url.rstrip('/')}/{url.short_code}",
            target_url=url.target_url,
            is_custom=url.is_custom,
            is_active=url.is_active,
            expires_at=url.expires_at,
            created_at=url.created_at,
        )

    def _validate_target(self, target: str) -> None:
        if len(target) > MAX_TARGET_URL_LENGTH:
            raise InvalidRequestError(
                f"target_url must be at most {MAX_TARGET_URL_LENGTH} characters"
            )
        # Shortening our own links would create redirect chains or loops.
        if urlsplit(target).hostname == urlsplit(self._settings.base_url).hostname:
            raise InvalidRequestError("target_url cannot point at this service")
