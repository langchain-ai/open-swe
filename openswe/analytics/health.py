"""Deployment-wide usage health from observed analytics projections."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import text

from openswe.analytics.queries import period_start
from openswe.database import connection
from openswe.database.analytics import reporting_metadata, workspace_id

_CACHE_PRESERVATION_ADDED_AT = datetime(2026, 11, 10, 3, 9, 59, tzinfo=UTC)

_CACHE_SQL = """
WITH observations AS (
    SELECT r.input_tokens, r.cache_read_tokens,
        r.input_tokens >= 0
            AND r.cache_read_tokens >= 0 AND r.cache_read_tokens <= r.input_tokens AS observed
    FROM run_projection r
    WHERE r.workspace_id = :workspace_id
      AND r.started_at >= :start AND r.started_at <= :as_of
)
SELECT
    sum(input_tokens) FILTER (WHERE observed) AS prompt_tokens,
    sum(cache_read_tokens) FILTER (WHERE observed) AS cache_read_tokens,
    count(*) FILTER (WHERE observed) AS observed_runs,
    count(*) FILTER (WHERE observed IS NOT TRUE) AS missing_usage_runs
FROM observations
"""


class UsageHealthMetadata(BaseModel):
    reporting_cutover_at: datetime | None
    collection_started_at: datetime | None
    last_processed_at: datetime | None
    data_source: Literal["event_projections"]
    completeness: Literal["not_started", "observed_events_only"]
    has_pending_events: bool
    has_failed_events: bool


class CacheHealth(BaseModel):
    hit_rate: float | None
    prompt_tokens: int | None
    cache_read_tokens: int | None
    preservation_added_at: datetime
    data_note: str = (
        "Older runs and some providers may omit cache counters. "
        "Only runs reporting both counters are included; "
        "run summaries may still contain partially reported messages."
    )
    period_predates_fix: bool
    missing_usage_runs: int
    observed_runs: int


class CacheObservation(BaseModel):
    prompt_tokens: int | None
    cache_read_tokens: int | None
    observed_runs: int
    missing_usage_runs: int

    async def hit_rate(self) -> float | None:
        if not self.prompt_tokens or self.cache_read_tokens is None:
            return None
        return self.cache_read_tokens / self.prompt_tokens


class UsageHealthPayload(UsageHealthMetadata):
    period: str
    as_of: datetime
    cache: CacheHealth

    @classmethod
    async def read(cls, period: str | None, *, as_of: datetime | None = None) -> UsageHealthPayload:
        normalized = period if period in {"24h", "7d", "30d", "all"} else "30d"
        as_of = as_of or datetime.now(UTC)
        requested_start = period_start(normalized, as_of=as_of)
        async with connection() as conn:
            await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            metadata = UsageHealthMetadata.model_validate(await reporting_metadata(conn))
            if metadata.reporting_cutover_at is None:
                raise RuntimeError("analytics reporting has not been activated")
            start = max(requested_start, metadata.reporting_cutover_at)
            rows = await conn.execute(
                text(_CACHE_SQL),
                {
                    "workspace_id": workspace_id(),
                    "start": start,
                    "as_of": as_of,
                },
            )
            total = CacheObservation.model_validate(dict(rows.mappings().one()))
        return cls(
            **metadata.model_dump(),
            period=normalized,
            as_of=as_of,
            cache=CacheHealth(
                hit_rate=await total.hit_rate(),
                prompt_tokens=total.prompt_tokens,
                cache_read_tokens=total.cache_read_tokens,
                preservation_added_at=_CACHE_PRESERVATION_ADDED_AT,
                period_predates_fix=start < _CACHE_PRESERVATION_ADDED_AT,
                missing_usage_runs=total.missing_usage_runs,
                observed_runs=total.observed_runs,
            ),
        )
