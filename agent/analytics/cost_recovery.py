"""Leased, globally paced cost lookup and projection confirmation."""

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Literal
from uuid import UUID, uuid4

from langsmith.utils import (
    LangSmithAuthError,
    LangSmithRateLimitError,
    LangSmithRequestTimeout,
    LangSmithUserError,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent import database
from agent.analytics.events import EventName, RunCostRecordedPayload, make_event
from agent.analytics.identity import opaque_id
from agent.analytics.outbox import enqueue
from agent.config import ENV
from agent.database.analytics import workspace_id
from agent.utils.langsmith import LangSmithCostUnavailable, get_langsmith_thread_cost
from agent.utils.tracing import tracing_project

logger = logging.getLogger(__name__)
FailureCode = Literal[
    "not_ingested",
    "rate_limited",
    "timeout",
    "transient",
    "configuration",
    "permission",
    "invalid_identifier",
]


@dataclass(frozen=True)
class Failure:
    code: FailureCode
    retryable: bool = True
    retry_after: float = 0


def classify_failure(exc: Exception) -> Failure:
    cause: BaseException | None = exc
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        response = getattr(cause, "response", None)
        status = getattr(cause, "status_code", None) or getattr(response, "status_code", None)
        if status == 429 or isinstance(cause, LangSmithRateLimitError):
            headers = getattr(response, "headers", {})
            raw = headers.get("retry-after", "0")
            try:
                delay = max(0.0, float(raw))
            except ValueError, TypeError:
                try:
                    delay = max(
                        0.0, (parsedate_to_datetime(raw) - datetime.now(UTC)).total_seconds()
                    )
                except ValueError, TypeError, OverflowError:
                    logger.warning("Invalid cost lookup Retry-After header")
                    delay = 0
            return Failure("rate_limited", retry_after=delay)
        if status in {401, 403} or isinstance(cause, LangSmithAuthError):
            return Failure("permission", False)
        if status in {400, 422} or isinstance(cause, LangSmithUserError):
            return Failure("invalid_identifier", False)
        if isinstance(cause, TimeoutError | LangSmithRequestTimeout):
            return Failure("timeout")
        cause = cause.__cause__
    if isinstance(exc, LangSmithCostUnavailable):
        return Failure("configuration", False)
    return Failure("transient")


def retry_delay(attempts: int) -> float:
    delays = (15, 30, 60, 120, 240, 900, 3600, 21600, 86400)
    return delays[min(max(attempts, 0), len(delays) - 1)] * random.uniform(0.8, 1.2)


async def enqueue_job(
    conn: AsyncConnection, invocation_id: str, thread_id: str, started_at: str | None
) -> None:
    run_id = opaque_id("run", invocation_id)
    if run_id is None:
        raise ValueError("Missing analytics run identity")
    await conn.execute(
        text("""
        INSERT INTO run_cost_refresh
            (workspace_id, run_id, scheduled_at, state, invocation_id, thread_id,
             invocation_started_at, project_name, retry_started_at, next_attempt_at)
        VALUES (:workspace, :run, clock_timestamp(), 'pending', :invocation, :thread,
                :started, :project, clock_timestamp(), clock_timestamp() + interval '15 seconds')
        ON CONFLICT (workspace_id, run_id) DO UPDATE SET
            state = 'pending', invocation_id = EXCLUDED.invocation_id,
            thread_id = EXCLUDED.thread_id, invocation_started_at = EXCLUDED.invocation_started_at,
            project_name = EXCLUDED.project_name, retry_started_at = EXCLUDED.retry_started_at,
            next_attempt_at = EXCLUDED.next_attempt_at
        WHERE run_cost_refresh.state = 'legacy_unmapped'
    """),
        {
            "workspace": workspace_id(),
            "run": run_id,
            "invocation": invocation_id,
            "thread": thread_id,
            "started": started_at,
            "project": tracing_project(),
        },
    )


async def maintain_jobs() -> None:
    async with database.transaction() as conn:
        await conn.execute(
            text("""
            UPDATE run_cost_refresh j SET state = 'complete', completed_at = clock_timestamp(),
                claim_token = NULL, lease_until = NULL, error_code = NULL
            WHERE j.workspace_id = :workspace AND j.state IN ('pending', 'leased', 'awaiting_delivery')
              AND EXISTS (SELECT 1 FROM latest_cost_projection p WHERE p.workspace_id = j.workspace_id
                  AND p.run_id = j.run_id AND p.cost_usd IS NOT NULL);
        """),
            {"workspace": workspace_id()},
        )
        await conn.execute(
            text("""
            UPDATE run_cost_refresh j SET state = 'needs_attention', error_code = 'delivery_dead_letter'
            WHERE j.workspace_id = :workspace AND j.state = 'awaiting_delivery'
              AND EXISTS (SELECT 1 FROM outbox o WHERE o.event_id = j.cost_event_id
                  AND o.workspace_id = j.workspace_id AND o.state = 'dead_letter')
        """),
            {"workspace": workspace_id()},
        )
        await conn.execute(
            text("""
            UPDATE run_cost_refresh SET state = 'needs_attention', error_code = 'exhausted',
                claim_token = NULL, lease_until = NULL
            WHERE workspace_id = :workspace AND state IN ('pending', 'leased', 'awaiting_delivery')
                AND retry_started_at < clock_timestamp() - interval '7 days'
                AND (lease_until IS NULL OR lease_until <= clock_timestamp())
        """),
            {"workspace": workspace_id()},
        )
        await conn.execute(
            text("""
            UPDATE run_cost_refresh SET invocation_id = NULL, thread_id = NULL,
                invocation_started_at = NULL, project_name = NULL
            WHERE workspace_id = :workspace AND invocation_id IS NOT NULL
                AND ((state = 'complete' AND completed_at < clock_timestamp() - interval '1 day')
                  OR (state = 'needs_attention' AND scheduled_at < clock_timestamp() - interval '30 days'))
        """),
            {"workspace": workspace_id()},
        )


@dataclass(frozen=True)
class Job:
    run_id: UUID
    token: UUID
    invocation_id: str
    thread_id: str
    started_at: str | None
    project: str
    attempts: int


async def claim_job() -> Job | None:
    token = uuid4()
    async with database.transaction() as conn:
        dispatcher = await conn.scalar(
            text("""
            UPDATE cost_recovery_dispatcher SET claim_token = :token,
                available_at = clock_timestamp() + interval '3 minutes'
            WHERE available_at <= clock_timestamp() RETURNING singleton
        """),
            {"token": token},
        )
        if not dispatcher:
            return None
        row = (
            (
                await conn.execute(
                    text("""
            WITH candidate AS (
                SELECT workspace_id, run_id FROM run_cost_refresh
                WHERE workspace_id = :workspace AND state IN ('pending', 'leased')
                  AND next_attempt_at <= clock_timestamp()
                  AND (lease_until IS NULL OR lease_until <= clock_timestamp())
                  AND retry_started_at >= clock_timestamp() - interval '7 days'
                  AND invocation_id IS NOT NULL
                ORDER BY next_attempt_at FOR UPDATE SKIP LOCKED LIMIT 1
            ) UPDATE run_cost_refresh j SET state = 'leased', claim_token = :token,
                lease_until = clock_timestamp() + interval '3 minutes',
                attempts = attempts + 1, last_attempt_at = clock_timestamp()
            FROM candidate c WHERE j.workspace_id = c.workspace_id AND j.run_id = c.run_id
            RETURNING j.*
        """),
                    {"workspace": workspace_id(), "token": token},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            await conn.execute(
                text(
                    "UPDATE cost_recovery_dispatcher SET available_at = clock_timestamp(), claim_token = NULL WHERE claim_token = :token"
                ),
                {"token": token},
            )
            return None
        return Job(
            row["run_id"],
            token,
            row["invocation_id"],
            row["thread_id"],
            row["invocation_started_at"],
            row["project_name"],
            row["attempts"],
        )


async def process_job(job: Job) -> None:
    failure = Failure("not_ingested")
    snapshot = None
    try:
        async with asyncio.timeout(120):
            snapshot = await get_langsmith_thread_cost(
                job.thread_id,
                job.invocation_id,
                run_only=True,
                lookup_start=job.started_at,
                project_name=job.project,
                strict=True,
            )
    except Exception as exc:
        failure = classify_failure(exc)
        logger.warning("Cost recovery lookup failed", extra={"cost_error_code": failure.code})
    async with database.transaction() as conn:
        active = await conn.scalar(
            text("""
            SELECT run_id FROM run_cost_refresh WHERE workspace_id = :workspace AND run_id = :run
                AND claim_token = :token AND state = 'leased' AND lease_until > clock_timestamp()
            FOR UPDATE
        """),
            {"workspace": workspace_id(), "run": job.run_id, "token": job.token},
        )
        if active is None:
            return
        event_id = None
        if snapshot is not None:
            now = datetime.now(UTC)
            event = make_event(
                event_name=EventName.RUN_COST_RECORDED,
                workspace_id=workspace_id(),
                producer="open-swe",
                producer_event_id=f"cost-recovery:{job.run_id}",
                occurred_at=now,
                environment=ENV.ANALYTICS_ENVIRONMENT.get(),
                run_id=job.run_id,
                payload=RunCostRecordedPayload(
                    cost_usd=Decimal(str(snapshot.total_cost)),
                    observation_revision=int(now.timestamp() * 1_000_000),
                    observed_at=now,
                    status="complete",
                    source="langsmith",
                ),
            )
            await enqueue(event, conn)
            event_id = event.event_id
        delay = max(retry_delay(job.attempts), failure.retry_after)
        await conn.execute(
            text("""
            UPDATE run_cost_refresh SET state = :state, cost_event_id = :event,
                error_code = :error, next_attempt_at = clock_timestamp() + :delay * interval '1 second',
                claim_token = NULL, lease_until = NULL
            WHERE workspace_id = :workspace AND run_id = :run AND claim_token = :token
        """),
            {
                "workspace": workspace_id(),
                "run": job.run_id,
                "token": job.token,
                "state": "awaiting_delivery"
                if event_id
                else "pending"
                if failure.retryable
                else "needs_attention",
                "event": event_id,
                "error": None if event_id else failure.code,
                "delay": delay,
            },
        )
        spacing = max(1, ENV.COST_RECOVERY_SPACING_SECONDS.get_int(15))
        await conn.execute(
            text("""
            UPDATE cost_recovery_dispatcher SET claim_token = NULL,
                available_at = clock_timestamp() + :delay * interval '1 second'
            WHERE claim_token = :token
        """),
            {
                "token": job.token,
                "delay": max(spacing, delay if failure.code == "rate_limited" else 0),
            },
        )


async def run_worker(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await maintain_jobs()
            if ENV.COST_RECOVERY_ENABLED.get_bool():
                job = await claim_job()
                if job is not None:
                    await process_job(job)
        except Exception as exc:
            logger.warning(
                "Cost recovery iteration failed", extra={"cost_error_type": type(exc).__name__}
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except TimeoutError:
            continue
