from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.models import ClickEvent
from app.schemas.analytics import AnalyticsResponse, Granularity, ReferrerCount, SeriesPoint

STEP = {Granularity.HOUR: timedelta(hours=1), Granularity.DAY: timedelta(days=1)}
# Caps the number of points per response, which bounds query cost and payload size.
MAX_RANGE = {Granularity.HOUR: timedelta(days=14), Granularity.DAY: timedelta(days=366)}
TOP_REFERRERS = 10


def truncate(ts: datetime, granularity: Granularity) -> datetime:
    ts = ts.astimezone(UTC)
    if granularity is Granularity.DAY:
        return ts.replace(hour=0, minute=0, second=0, microsecond=0)
    return ts.replace(minute=0, second=0, microsecond=0)


class AnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def summarize(
        self,
        *,
        url_id: int,
        short_code: str,
        granularity: Granularity,
        start: datetime | None,
        end: datetime | None,
    ) -> AnalyticsResponse:
        end = end or datetime.now(UTC)
        start = start or end - timedelta(days=7)
        # Align to bucket boundaries: [start, end) covers whole buckets only.
        start = truncate(start, granularity)
        end = truncate(end, granularity) + STEP[granularity]
        if end - start > MAX_RANGE[granularity]:
            raise InvalidRequestError(
                f"range too large for {granularity.value} granularity "
                f"(max {MAX_RANGE[granularity].days} days)"
            )

        in_range = (
            ClickEvent.url_id == url_id,
            ClickEvent.occurred_at >= start,
            ClickEvent.occurred_at < end,
        )
        bucket = func.date_trunc(granularity.value, ClickEvent.occurred_at, "UTC").label("bucket")
        series_rows = await self._session.execute(
            select(bucket, func.count()).where(*in_range).group_by(bucket).order_by(bucket)
        )
        counts = {row[0]: row[1] for row in series_rows}

        unique_visitors = await self._session.scalar(
            select(func.count(func.distinct(ClickEvent.visitor_hash))).where(*in_range)
        )

        referrer = func.coalesce(ClickEvent.referrer_host, "(direct)").label("referrer")
        referrer_rows = await self._session.execute(
            select(referrer, func.count().label("clicks"))
            .where(*in_range)
            .group_by(referrer)
            .order_by(func.count().desc(), referrer)
            .limit(TOP_REFERRERS)
        )

        series = _zero_fill(counts, start, end, STEP[granularity])
        return AnalyticsResponse(
            short_code=short_code,
            granularity=granularity,
            start=start,
            end=end,
            total_clicks=sum(point.clicks for point in series),
            unique_visitors=unique_visitors or 0,
            series=series,
            top_referrers=[ReferrerCount(referrer=r, clicks=c) for r, c in referrer_rows],
        )


def _zero_fill(
    counts: dict[datetime, int], start: datetime, end: datetime, step: timedelta
) -> list[SeriesPoint]:
    """Emit every bucket in range, so charts don't have to interpolate gaps."""
    points = []
    cursor = start
    while cursor < end:
        points.append(SeriesPoint(bucket=cursor, clicks=counts.get(cursor, 0)))
        cursor += step
    return points
