from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class Granularity(StrEnum):
    HOUR = "hour"
    DAY = "day"


class SeriesPoint(BaseModel):
    bucket: datetime
    clicks: int


class ReferrerCount(BaseModel):
    referrer: str
    clicks: int


class AnalyticsResponse(BaseModel):
    short_code: str
    granularity: Granularity
    start: datetime
    end: datetime
    total_clicks: int
    unique_visitors: int
    series: list[SeriesPoint]
    top_referrers: list[ReferrerCount]
