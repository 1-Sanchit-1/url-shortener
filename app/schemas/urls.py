from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl


class ShortenRequest(BaseModel):
    # HttpUrl only accepts http/https, which rules out javascript:, data: and file: targets.
    target_url: HttpUrl
    custom_alias: str | None = Field(default=None, examples=["spring-sale"])
    expires_at: AwareDatetime | None = None


class UpdateUrlRequest(BaseModel):
    """Partial update. Send ``expires_at: null`` to remove an expiry."""

    target_url: HttpUrl | None = None
    is_active: bool | None = None
    expires_at: AwareDatetime | None = None


class UrlResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    short_code: str
    short_url: str
    target_url: str
    is_custom: bool
    is_active: bool
    expires_at: datetime | None
    created_at: datetime


class UrlPage(BaseModel):
    items: list[UrlResponse]
    next_cursor: int | None = Field(
        description="Pass as `cursor` to fetch the next page; null on the last page."
    )
