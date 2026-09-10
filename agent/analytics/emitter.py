"""Fail-soft lifecycle event capture at product transition points."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from agent.analytics.capture import fail_soft
from agent.analytics.events import (
    EntryPoint,
    EventName,
    FeedbackSubmittedPayload,
    FindingStatePayload,
    FindingSurfacedPayload,
    PROpenedPayload,
    PRRunLinkedPayload,
    PRStatePayload,
    ReviewPublishedPayload,
    RunCanceledPayload,
    RunCompletedPayload,
    RunCostRecordedPayload,
    RunFailedPayload,
    RunStartedPayload,
    StrictPayload,
    TaskAcceptedPayload,
    TaskMarkedCompletePayload,
    TaskReworkRequestedPayload,
    make_event,
    person_uuid,
    subject_uuid,
)
from agent.analytics.outbox import enqueue
from agent.config import ENV

_ENTRY_POINTS = {
    "dashboard": EntryPoint.DASHBOARD,
    "web": EntryPoint.DASHBOARD,
    "github": EntryPoint.GITHUB,
    "github_issue": EntryPoint.GITHUB,
    "slack": EntryPoint.SLACK,
    "schedule": EntryPoint.SCHEDULED,
    "scheduled": EntryPoint.SCHEDULED,
    "desktop": EntryPoint.DESKTOP,
    "api": EntryPoint.API,
    "linear": EntryPoint.LINEAR,
}


def workspace_id() -> UUID:
    return UUID(ENV.ANALYTICS_WORKSPACE_ID.require())


def opaque_id(kind: str, value: str | int | None) -> UUID | None:
    normalized = str(value or "").strip()
    return subject_uuid(workspace_id(), kind, normalized) if normalized else None


def opaque_person(provider: str, immutable_id: str | int | None) -> UUID | None:
    normalized = str(immutable_id or "").strip().lower()
    return person_uuid(workspace_id(), provider, normalized) if normalized else None


def entry_point(source: str | None) -> EntryPoint:
    return _ENTRY_POINTS.get(str(source or "").lower(), EntryPoint.UNKNOWN)


@fail_soft
async def emit(
    event_name: EventName,
    producer_event_id: str,
    payload: StrictPayload,
    *,
    occurred_at: datetime | None = None,
    source: str | None = None,
    source_version: int | None = None,
    **identifiers: object,
) -> None:
    event = make_event(
        event_name=event_name,
        workspace_id=workspace_id(),
        producer="open-swe",
        producer_event_id=producer_event_id,
        occurred_at=occurred_at or datetime.now(UTC),
        payload=payload,
        environment=ENV.ANALYTICS_ENVIRONMENT.get(),
        source_version=source_version,
        entry_point=entry_point(source),
        **identifiers,
    )
    await enqueue(event)


@fail_soft
async def run_started(
    *,
    run_key: str,
    thread_key: str,
    model: str | None,
    source: str | None,
    immutable_person_key: str | int | None,
    repository_key: str | None,
    occurred_at: datetime | None = None,
) -> None:
    model_id = opaque_id("model", model)
    from agent.analytics.directory import upsert_model

    await upsert_model(model)
    await emit(
        EventName.RUN_STARTED,
        f"run:{run_key}:started",
        RunStartedPayload(
            configured_model_id=model_id,
            effective_model_id=model_id,
            model_attribution_quality="configured" if model_id else "unavailable",
        ),
        occurred_at=occurred_at,
        source=source,
        run_id=opaque_id("run", run_key),
        preparation_run_id=opaque_id("preparation_run", run_key),
        thread_id=opaque_id("thread", thread_key),
        task_id=opaque_id("task", thread_key),
        user_id=opaque_person("github", immutable_person_key),
        repository_id=opaque_id("repository", repository_key),
        model_id=model_id,
    )


@fail_soft
async def run_terminal(
    *,
    run_key: str,
    thread_key: str,
    status: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    failure_code: str | None = None,
    producer_suffix: str = "terminal",
) -> None:
    if status == "success":
        name = EventName.RUN_COMPLETED
        payload: StrictPayload = RunCompletedPayload(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )
    elif status in {"interrupted", "canceled"}:
        name = EventName.RUN_CANCELED
        payload = RunCanceledPayload(
            cancellation_source="superseded" if status == "interrupted" else "user"
        )
    else:
        name = EventName.RUN_FAILED
        payload = RunFailedPayload(
            failure_class="timeout" if status == "timeout" else "error",
            failure_code=failure_code,
        )
    await emit(
        name,
        f"run:{run_key}:{producer_suffix}:{name.value}",
        payload,
        run_id=opaque_id("run", run_key),
        preparation_run_id=opaque_id("preparation_run", run_key),
        thread_id=opaque_id("thread", thread_key),
        task_id=opaque_id("task", thread_key),
    )


@fail_soft
async def run_cost(
    *,
    run_key: str,
    cost_usd: float | None,
    revision: int = 1,
    status: Literal["complete", "partial", "unavailable"] = "complete",
    source: Literal["provider", "langsmith", "unavailable"] = "langsmith",
    missing_reason: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
) -> None:
    await emit(
        EventName.RUN_COST_RECORDED,
        f"run:{run_key}:cost:{revision}",
        RunCostRecordedPayload(
            cost_usd=Decimal(str(cost_usd)) if cost_usd is not None else None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            status=status,
            source=source,
            missing_reason=missing_reason,
            observation_revision=revision,
            observed_at=datetime.now(UTC),
        ),
        run_id=opaque_id("run", run_key),
    )


@fail_soft
async def pr_opened(
    *,
    owner: str,
    repo: str,
    number: int,
    run_key: str,
    model: str | None,
    source: str | None,
    repository_private: bool | None,
    occurred_at: datetime,
) -> None:
    pr_key = f"{owner.lower()}/{repo.lower()}#{number}"
    model_id = opaque_id("model", model)
    pr_id = opaque_id("pr", pr_key)
    run_id = opaque_id("run", run_key)
    repository_id = opaque_id("repository", f"{owner.lower()}/{repo.lower()}")
    if pr_id is None or run_id is None or repository_id is None:
        return
    from agent.analytics.directory import upsert_model, upsert_repository

    await upsert_model(model)
    await upsert_repository(full_name=f"{owner}/{repo}", private=repository_private)
    await emit(
        EventName.PR_OPENED,
        f"pr:{pr_key}:opened",
        PROpenedPayload(
            opening_run_id=run_id,
            originating_model_id=model_id,
            model_attribution_quality="configured" if model_id else "unavailable",
            repository_private=repository_private,
        ),
        occurred_at=occurred_at,
        source=source,
        pr_id=pr_id,
        run_id=run_id,
        repository_id=repository_id,
        model_id=model_id,
    )
    await emit(
        EventName.PR_RUN_LINKED,
        f"pr:{pr_key}:run:{run_key}:opening",
        PRRunLinkedPayload(link_role="opening"),
        occurred_at=occurred_at,
        source=source,
        pr_id=pr_id,
        run_id=run_id,
        repository_id=repository_id,
        model_id=model_id,
    )


@fail_soft
async def pr_state(
    *,
    owner: str,
    repo: str,
    number: int,
    action: str,
    merged: bool,
    source_version: int | None,
    occurred_at: datetime,
) -> None:
    name = (
        EventName.PR_MERGED
        if merged
        else EventName.PR_REOPENED
        if action == "reopened"
        else EventName.PR_CLOSED_WITHOUT_MERGE
    )
    pr_key = f"{owner.lower()}/{repo.lower()}#{number}"
    await emit(
        name,
        f"github:pr:{pr_key}:{source_version or occurred_at.isoformat()}:{name.value}",
        PRStatePayload(previous_state=None),
        occurred_at=occurred_at,
        source="github",
        source_version=source_version,
        pr_id=opaque_id("pr", pr_key),
        repository_id=opaque_id("repository", f"{owner.lower()}/{repo.lower()}"),
    )


@fail_soft
async def task_marked_complete(thread_key: str, *, source: str, auto: bool = False) -> None:
    await emit(
        EventName.TASK_MARKED_COMPLETE,
        f"task:{thread_key}:complete:{source}",
        TaskMarkedCompletePayload(completion_source="pr_terminal" if auto else "user"),
        source=source,
        task_id=opaque_id("task", thread_key),
        thread_id=opaque_id("thread", thread_key),
    )


@fail_soft
async def task_accepted(thread_key: str, *, source: str, actor_key: str | int | None) -> None:
    await emit(
        EventName.TASK_ACCEPTED,
        f"task:{thread_key}:accepted:{source}",
        TaskAcceptedPayload(acceptance_source="explicit_user"),
        source=source,
        task_id=opaque_id("task", thread_key),
        thread_id=opaque_id("thread", thread_key),
        user_id=opaque_person("github", actor_key),
    )


@fail_soft
async def task_rework(
    thread_key: str,
    *,
    source: str,
    scope: Literal["minor", "major"],
    reason: Literal["explicit_user", "plan_review", "pr_reopened"],
) -> None:
    await emit(
        EventName.TASK_REWORK_REQUESTED,
        f"task:{thread_key}:rework:{source}:{datetime.now(UTC).isoformat()}",
        TaskReworkRequestedPayload(scope=scope, request_source=reason),
        source=source,
        task_id=opaque_id("task", thread_key),
        thread_id=opaque_id("thread", thread_key),
    )


@fail_soft
async def feedback_submitted(
    *, run_key: str, person_key: str, rating: int, producer_version: str
) -> None:
    sentiment = "negative" if rating <= 2 else "neutral" if rating == 3 else "positive"
    await emit(
        EventName.FEEDBACK_SUBMITTED,
        f"feedback:{run_key}:{person_key}:{producer_version}",
        FeedbackSubmittedPayload(sentiment=sentiment, rating=rating),
        source="slack",
        run_id=opaque_id("run", run_key),
        user_id=opaque_person("slack", person_key),
    )


@fail_soft
async def review_published(
    *, thread_key: str, owner: str, repo: str, number: int, head_sha: str, finding_count: int
) -> None:
    pr_key = f"{owner.lower()}/{repo.lower()}#{number}"
    await emit(
        EventName.REVIEW_PUBLISHED,
        f"review:{thread_key}:{head_sha}:published",
        ReviewPublishedPayload(finding_count=finding_count),
        source="github",
        review_id=opaque_id("review", f"{thread_key}:{head_sha}"),
        pr_id=opaque_id("pr", pr_key),
        repository_id=opaque_id("repository", f"{owner.lower()}/{repo.lower()}"),
    )


@fail_soft
async def finding_transition(
    *,
    thread_key: str,
    finding_key: str,
    head_sha: str,
    owner: str,
    repo: str,
    number: int,
    state: str,
    severity: Literal["low", "medium", "high", "critical"],
    category: str,
    version: str,
) -> None:
    pr_key = f"{owner.lower()}/{repo.lower()}#{number}"
    finding_id = opaque_id("finding", f"{thread_key}:{finding_key}")
    pr_id = opaque_id("pr", pr_key)
    repository_id = opaque_id("repository", f"{owner.lower()}/{repo.lower()}")
    if finding_id is None or pr_id is None or repository_id is None:
        return
    if state == "surfaced":
        await emit(
            EventName.FINDING_SURFACED,
            f"finding:{thread_key}:{finding_key}:surfaced:{version}",
            FindingSurfacedPayload(severity=severity, category=category or "unknown"),
            source="github",
            finding_id=finding_id,
            review_id=opaque_id("review", f"{thread_key}:{head_sha}"),
            pr_id=pr_id,
            repository_id=repository_id,
        )
        return
    name = {
        "resolved": EventName.FINDING_RESOLVED,
        "dismissed": EventName.FINDING_DISMISSED,
        "reopened": EventName.FINDING_REOPENED,
    }.get(state)
    if name is not None:
        await emit(
            name,
            f"finding:{thread_key}:{finding_key}:{state}:{version}",
            FindingStatePayload(previous_state=None),
            source="github",
            finding_id=finding_id,
            pr_id=pr_id,
            repository_id=repository_id,
        )
