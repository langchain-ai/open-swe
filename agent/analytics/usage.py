"""Product usage capture backed only by PostgreSQL analytics events."""

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text

from agent.analytics import database, directory, emitter
from agent.analytics.capture import fail_soft
from agent.analytics.events import (
    EventName,
    RunCanceledPayload,
    RunCompletedPayload,
    RunCostRecordedPayload,
    RunFailedPayload,
    StrictPayload,
)
from agent.analytics.identity import opaque_id
from agent.review.findings import coerce_finding, is_surfaced
from agent.utils.json_types import as_json_object
from agent.utils.run_usage import RunUsageSummary


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float) and math.isfinite(value) and value > 0:
        try:
            return datetime.fromtimestamp(value / 1000 if value > 10_000_000_000 else value, UTC)
        except OverflowError, OSError, ValueError:
            return None
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
        except ValueError:
            return None
    return None


def _count(value: object) -> int | None:
    return max(0, value) if isinstance(value, int) and not isinstance(value, bool) else None


async def _run_status(run_id: UUID) -> dict[str, bool]:
    async with database.connection() as conn:
        result = await conn.execute(
            text(
                """
                SELECT
                    EXISTS (SELECT 1 FROM run_projection WHERE workspace_id = :workspace
                            AND run_id = :run AND started_at IS NOT NULL)
                    OR EXISTS (SELECT 1 FROM outbox WHERE workspace_id = :workspace
                               AND event_body ->> 'run_id' = :run_text
                               AND event_body ->> 'event_name' = 'run.started') AS started,
                    EXISTS (SELECT 1 FROM run_projection WHERE workspace_id = :workspace
                            AND run_id = :run AND terminal_at IS NOT NULL)
                    OR EXISTS (SELECT 1 FROM outbox WHERE workspace_id = :workspace
                               AND event_body ->> 'run_id' = :run_text
                               AND event_body ->> 'event_name' IN
                                   ('run.completed', 'run.failed', 'run.canceled')) AS finished,
                    EXISTS (SELECT 1 FROM latest_cost_projection WHERE workspace_id = :workspace
                            AND run_id = :run AND cost_usd IS NOT NULL)
                    OR EXISTS (SELECT 1 FROM outbox WHERE workspace_id = :workspace
                               AND event_body ->> 'run_id' = :run_text
                               AND event_body ->> 'event_name' = 'run.cost_recorded'
                               AND event_body -> 'payload' ->> 'cost_usd' IS NOT NULL) AS cost_known,
                    EXISTS (SELECT 1 FROM run_cost_refresh WHERE workspace_id = :workspace
                            AND run_id = :run) AS scheduled
                """
            ),
            {"workspace": database.workspace_id(), "run": run_id, "run_text": str(run_id)},
        )
        return dict(result.mappings().one())


@fail_soft
async def record_agent_invocation_usage(
    *,
    invocation_id: str,
    thread_id: str,
    github_login: str | None,
    user_email: str | None,
    model_id: str,
    effort: str | None,
    source: str | None,
    github_user_id: str | int | None = None,
    repository: str | None = None,
) -> None:
    if not invocation_id or not thread_id:
        return
    person = await directory.resolve_person(
        immutable_person_key=github_user_id, github_login=github_login, email=user_email
    )
    await emitter.run_started(
        run_key=invocation_id,
        thread_key=thread_id,
        model=model_id,
        source=source,
        immutable_person_key=github_user_id or github_login,
        repository_key=repository,
        user_id=person,
    )


async def record_agent_invocation_completion(
    *,
    invocation_id: str,
    usage: RunUsageSummary | None,
    status: str = "success",
    thread_id: str = "",
) -> bool:
    if not invocation_id or not database.configured():
        return False
    run_id = opaque_id("run", invocation_id)
    if run_id is None:
        return False
    current = await _run_status(run_id)
    if not current["started"] or current["finished"]:
        return False
    counts = RunCompletedPayload(
        input_tokens=usage.input_tokens if usage else None,
        output_tokens=usage.output_tokens if usage else None,
        total_tokens=usage.total_tokens if usage else None,
    )
    if status == "success":
        name = EventName.RUN_COMPLETED
        payload: StrictPayload = counts
    elif status in {"interrupted", "canceled"}:
        name = EventName.RUN_CANCELED
        payload = RunCanceledPayload(
            cancellation_source="superseded" if status == "interrupted" else "user",
            input_tokens=counts.input_tokens,
            output_tokens=counts.output_tokens,
            total_tokens=counts.total_tokens,
        )
    else:
        name = EventName.RUN_FAILED
        payload = RunFailedPayload(
            failure_class="timeout" if status == "timeout" else "error",
            input_tokens=counts.input_tokens,
            output_tokens=counts.output_tokens,
            total_tokens=counts.total_tokens,
        )
    return await emitter.enqueue_event(
        name,
        f"run:{invocation_id}:middleware:{name.value}",
        payload,
        run_id=run_id,
        preparation_run_id=opaque_id("preparation_run", invocation_id),
        thread_id=opaque_id("thread", thread_id),
        task_id=opaque_id("task", thread_id),
    )


async def agent_invocation_needs_cost_refresh(*, invocation_id: str) -> bool:
    if not invocation_id or not database.configured():
        return False
    run_id = opaque_id("run", invocation_id)
    if run_id is None:
        return False
    current = await _run_status(run_id)
    return (
        current["started"]
        and current["finished"]
        and not current["cost_known"]
        and not current["scheduled"]
    )


async def mark_agent_invocation_cost_refresh_scheduled(*, invocation_id: str) -> None:
    if not invocation_id or not database.configured():
        return
    run_id = opaque_id("run", invocation_id)
    if run_id is None or not (await _run_status(run_id))["finished"]:
        return
    async with database.transaction() as conn:
        await conn.execute(
            text(
                "INSERT INTO run_cost_refresh (workspace_id, run_id, scheduled_at) "
                "VALUES (:workspace, :run, clock_timestamp()) ON CONFLICT DO NOTHING"
            ),
            {"workspace": database.workspace_id(), "run": run_id},
        )


async def record_agent_invocation_cost(*, invocation_id: str, cost_usd: float) -> None:
    if not database.configured():
        raise RuntimeError("Usage analytics is unavailable")
    if not invocation_id:
        return
    if not math.isfinite(cost_usd) or cost_usd < 0:
        return
    run_id = opaque_id("run", invocation_id)
    if run_id is None or not (await _run_status(run_id))["started"]:
        return
    observed_at = datetime.now(UTC)
    revision = int(observed_at.timestamp() * 1_000_000)
    await emitter.enqueue_event(
        EventName.RUN_COST_RECORDED,
        f"run:{invocation_id}:cost:{revision}",
        RunCostRecordedPayload(
            cost_usd=Decimal(str(cost_usd)),
            observation_revision=revision,
            observed_at=observed_at,
            status="complete",
            source="langsmith",
        ),
        run_id=run_id,
    )


@fail_soft
async def record_agent_pr_usage(
    *,
    thread_id: str | None,
    github_login: str | None,
    user_email: str | None,
    owner: str,
    repo: str,
    pr_number: int,
    pr_url: str | None,
    head: str,
    base: str,
    additions: int = 0,
    deletions: int = 0,
    changed_files: int = 0,
    state: str | None = None,
    merged: bool = False,
    created_at: object = None,
    merged_at: object = None,
    invocation_id: str | None = None,
    model_id: str | None = None,
    source: str | None = None,
    repository_private: bool | None = None,
) -> None:
    if not owner or not repo or not pr_number:
        return
    now = datetime.now(UTC)
    person = await directory.resolve_person(github_login=github_login, email=user_email)
    await emitter.pr_opened(
        owner=owner,
        repo=repo,
        number=pr_number,
        run_key=invocation_id,
        model=model_id,
        source=source,
        repository_private=repository_private,
        occurred_at=_timestamp(created_at) or now,
    )
    await emitter.pr_observed(
        owner=owner,
        repo=repo,
        number=pr_number,
        occurred_at=now,
        user_id=person,
        additions=_count(additions),
        deletions=_count(deletions),
        changed_files=_count(changed_files),
    )
    if merged or state == "closed":
        await emitter.pr_state(
            owner=owner,
            repo=repo,
            number=pr_number,
            action="closed",
            merged=merged,
            source_version=None,
            occurred_at=_timestamp(merged_at) or now,
        )


@fail_soft
async def update_agent_pr_usage_from_webhook(payload: dict[str, Any]) -> None:
    pr = as_json_object(payload.get("pull_request"))
    repository = as_json_object(payload.get("repository"))
    owner = as_json_object(repository.get("owner")).get("login")
    repo = repository.get("name")
    number = pr.get("number")
    if not isinstance(owner, str) or not isinstance(repo, str) or not isinstance(number, int):
        return
    # Observations can precede opening capture; reports join only agent-created PRs.
    observed_at = _timestamp(pr.get("updated_at")) or datetime.now(UTC)
    await emitter.pr_observed(
        owner=owner,
        repo=repo,
        number=number,
        occurred_at=observed_at,
        additions=_count(pr.get("additions")),
        deletions=_count(pr.get("deletions")),
        changed_files=_count(pr.get("changed_files")),
    )
    action = payload.get("action")
    if action in {"closed", "reopened"}:
        merged = pr.get("merged") is True
        await emitter.pr_state(
            owner=owner,
            repo=repo,
            number=number,
            action=action,
            merged=merged,
            source_version=None,
            occurred_at=(
                _timestamp(pr.get("merged_at"))
                if merged
                else _timestamp(pr.get("closed_at"))
                if action == "closed"
                else None
            )
            or observed_at,
        )


def _surfaced(finding: Mapping[str, Any]) -> bool:
    record = coerce_finding(dict(finding))
    return record is not None and is_surfaced(record)


def _human_replies(finding: Mapping[str, Any]) -> int:
    interactions = finding.get("interactions")
    if isinstance(interactions, list):
        return sum(
            1
            for item in interactions
            if isinstance(item, dict) and item.get("kind") == "human_reply"
        )
    return int(bool(finding.get("last_human_reply_at")))


async def _observe_finding(
    thread_id: str,
    finding: Mapping[str, Any],
    *,
    owner: str,
    repo: str,
    number: int,
    head_sha: str,
    published: bool,
) -> None:
    finding_id = finding.get("id")
    if not isinstance(finding_id, str) or not finding_id:
        return
    raw_state = finding.get("status")
    state: Literal["open", "resolved", "dismissed"] = (
        raw_state if raw_state in {"resolved", "dismissed"} else "open"
    )
    now = datetime.now(UTC)
    await emitter.finding_observed(
        thread_key=thread_id,
        finding_key=finding_id,
        owner=owner,
        repo=repo,
        number=number,
        head_sha=head_sha,
        observed_at=now,
        recorded_at=now if published else None,
        surfaced_at=now if _surfaced(finding) else None,
        state=state,
        severity=str(finding.get("severity") or ""),
        category=str(finding.get("category") or ""),
        first_seen_sha=str(finding.get("first_seen_sha") or "") or None,
        resolved_sha=str(finding.get("last_confirmed_sha") or "") if state == "resolved" else None,
        human_replies=_human_replies(finding),
    )


@fail_soft
async def record_reviewer_publication(
    *,
    thread_id: str,
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    findings: Sequence[Mapping[str, Any]],
) -> None:
    await emitter.review_published(
        thread_key=thread_id,
        owner=owner,
        repo=repo,
        number=pr_number,
        head_sha=head_sha,
        finding_count=sum(1 for finding in findings if finding.get("first_seen_sha") == head_sha),
    )
    for finding in findings:
        await _observe_finding(
            thread_id,
            finding,
            owner=owner,
            repo=repo,
            number=pr_number,
            head_sha=head_sha,
            published=True,
        )


@fail_soft
async def record_reviewer_finding_state(thread_id: str, finding: Mapping[str, Any]) -> None:
    pr = as_json_object(finding.get("pr"))
    owner = pr.get("owner") or finding.get("owner")
    repo = pr.get("name") or finding.get("repo")
    number = pr.get("number") or finding.get("pr_number")
    if not isinstance(owner, str) or not isinstance(repo, str) or not isinstance(number, int):
        return
    await _observe_finding(
        thread_id,
        finding,
        owner=owner,
        repo=repo,
        number=number,
        head_sha=str(finding.get("last_confirmed_sha") or ""),
        published=False,
    )
