from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl


class ShortenRequest(BaseModel):
    # HttpUrl only accepts http/https, which rules out javascript:, data: and file: targets.
    target_url: HttpUrl
    custom_alias: str | None = Field(default=None, examples=["spring-sale"])
    expires_at: AwareDatetime | None = None

    def target(self) -> str:
        return str(self.target_url)


class UrlResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    short_code: str
    short_url: str
    target_url: str
    is_custom: bool
    is_active: bool
    expires_at: datetime | None
    created_at: datetime
