from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import InvalidRequestError, NotFoundError
from app.core.security import Principal
from app.models import Url
from app.models.url import MAX_TARGET_URL_LENGTH
from app.repositories import urls as url_repo
from app.schemas.urls import ShortenRequest, UpdateUrlRequest, UrlPage, UrlResponse
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

    async def shorten(self, request: ShortenRequest, owner_id: int) -> Url:
        target = str(request.target_url)
        self._validate_target(target)
        self._validate_expiry(request.expires_at)

        if request.custom_alias is not None:
            url = await url_repo.insert_with_alias(
                self._session,
                alias=validate_alias(request.custom_alias),
                target_url=target,
                expires_at=request.expires_at,
                owner_id=owner_id,
            )
        else:
            url = await url_repo.insert_with_generated_code(
                self._session,
                target_url=target,
                expires_at=request.expires_at,
                length=self._settings.short_code_length,
                max_attempts=self._settings.short_code_max_attempts,
                owner_id=owner_id,
            )
        await self._session.commit()
        return url

    async def resolve(self, short_code: str) -> Link | None:
        url = await url_repo.get_by_code(self._session, short_code)
        if url is None:
            return None
        return Link(
            url_id=url.id,
            target_url=url.target_url,
            is_active=url.is_active,
            expires_at=url.expires_at,
        )

    async def get_for(self, short_code: str, principal: Principal) -> Url:
        url = await url_repo.get_by_code(self._session, short_code)
        # Another user's link is reported as missing rather than forbidden, so the
        # API can't be used to probe which codes exist.
        if url is None or (not principal.is_admin and url.owner_id != principal.user_id):
            raise NotFoundError("short link not found")
        return url

    async def update(self, short_code: str, body: UpdateUrlRequest, principal: Principal) -> Url:
        url = await self.get_for(short_code, principal)
        if body.target_url is not None:
            target = str(body.target_url)
            self._validate_target(target)
            url.target_url = target
        if body.is_active is not None:
            url.is_active = body.is_active
        if "expires_at" in body.model_fields_set:
            self._validate_expiry(body.expires_at)
            url.expires_at = body.expires_at
        await self._session.commit()
        return url

    async def delete(self, short_code: str, principal: Principal) -> Url:
        url = await self.get_for(short_code, principal)
        url.deleted_at = datetime.now(UTC)
        url.is_active = False
        await self._session.commit()
        return url

    async def list_page(
        self, *, owner_id: int | None, limit: int, before_id: int | None
    ) -> UrlPage:
        rows = await url_repo.list_page(
            self._session, owner_id=owner_id, limit=limit + 1, before_id=before_id
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        return UrlPage(
            items=[self.to_response(url) for url in rows],
            next_cursor=rows[-1].id if has_more else None,
        )

    def to_response(self, url: Url) -> UrlResponse:
        return UrlResponse(
            id=url.id,
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

    @staticmethod
    def _validate_expiry(expires_at: datetime | None) -> None:
        if expires_at is not None and expires_at <= datetime.now(UTC):
            raise InvalidRequestError("expires_at must be in the future")
