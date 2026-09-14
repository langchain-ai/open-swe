"""Storage-neutral analytics event contract."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

_FORBIDDEN_KEYS = {
    "prompt",
    "response",
    "feedback_text",
    "source_code",
    "diff",
    "branch",
    "branch_name",
    "path",
    "raw_path",
    "email",
    "display_name",
}

EVENT_NAMESPACE = UUID("22baaec2-b143-5686-b408-79f30584a831")
PERSON_NAMESPACE = UUID("6e711fef-ae6f-5307-a8bf-893b2bb8546d")
SUBJECT_NAMESPACE = UUID("0384a662-ef87-5c07-9d02-4fa90f14c860")
SCHEMA_VERSION = 1


class EventName(StrEnum):
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELED = "run.canceled"
    RUN_COST_RECORDED = "run.cost_recorded"
    FEEDBACK_SUBMITTED = "user.feedback_submitted"
    FEEDBACK_WITHDRAWN = "user.feedback_withdrawn"
    TASK_MARKED_COMPLETE = "task.marked_complete"
    TASK_ACCEPTED = "task.accepted"
    TASK_REWORK_REQUESTED = "task.rework_requested"
    PR_OPENED = "pr.opened"
    PR_OBSERVED = "pr.observed"
    PR_RUN_LINKED = "pr.run_linked"
    PR_MERGED = "pr.merged"
    PR_CLOSED_WITHOUT_MERGE = "pr.closed_without_merge"
    PR_REOPENED = "pr.reopened"
    REVIEW_PUBLISHED = "review.published"
    FINDING_SURFACED = "finding.surfaced"
    FINDING_OBSERVED = "finding.observed"
    FINDING_RESOLVED = "finding.resolved"
    FINDING_DISMISSED = "finding.dismissed"
    FINDING_REOPENED = "finding.reopened"


class EntryPoint(StrEnum):
    DASHBOARD = "dashboard"
    GITHUB = "github"
    SLACK = "slack"
    SCHEDULED = "scheduled"
    DESKTOP = "desktop"
    API = "api"
    LINEAR = "linear"
    UNKNOWN = "unknown"


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunStartedPayload(StrictPayload):
    configured_model_id: UUID | None = None
    effective_model_id: UUID | None = None
    model_attribution_quality: Literal["effective", "configured", "unavailable"]


class RunCompletedPayload(StrictPayload):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class RunFailedPayload(RunCompletedPayload):
    failure_class: Literal["error", "timeout", "unavailable"]
    failure_code: str | None = Field(default=None, max_length=100)


class RunCanceledPayload(RunCompletedPayload):
    cancellation_source: Literal["user", "system", "superseded", "unavailable"]


class RunCostRecordedPayload(StrictPayload):
    cost_usd: Decimal | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    status: Literal["complete", "partial", "unavailable"]
    source: Literal["provider", "langsmith", "unavailable"]
    missing_reason: str | None = Field(default=None, max_length=200)
    observation_revision: int = Field(ge=1)
    observed_at: datetime


class FeedbackSubmittedPayload(StrictPayload):
    sentiment: Literal["positive", "neutral", "negative"]
    rating: int | None = Field(default=None, ge=1, le=5)


class FeedbackWithdrawnPayload(StrictPayload):
    submission_event_id: UUID


class TaskMarkedCompletePayload(StrictPayload):
    completion_source: Literal["user", "pr_terminal"]


class TaskAcceptedPayload(StrictPayload):
    acceptance_source: Literal["explicit_user", "external_system"]


class TaskReworkRequestedPayload(StrictPayload):
    scope: Literal["minor", "major"]
    request_source: Literal["explicit_user", "plan_review", "pr_reopened"]


class PROpenedPayload(StrictPayload):
    opening_run_id: UUID | None = None
    originating_model_id: UUID | None = None
    model_attribution_quality: Literal["effective", "configured", "unavailable"]
    repository_private: bool | None = None


class PRObservedPayload(StrictPayload):
    additions: int | None = Field(default=None, ge=0)
    deletions: int | None = Field(default=None, ge=0)
    changed_files: int | None = Field(default=None, ge=0)


class FindingObservedPayload(StrictPayload):
    recorded_at: AwareDatetime | None = None
    surfaced_at: AwareDatetime | None = None
    resolved_at: AwareDatetime | None = None
    current_state: Literal["open", "resolved", "dismissed"]
    severity: str = Field(max_length=100)
    category: str = Field(max_length=100)
    first_seen_revision_id: UUID | None = None
    resolved_revision_id: UUID | None = None
    human_replies: int = Field(ge=0)


class PRRunLinkedPayload(StrictPayload):
    link_role: Literal["opening", "follow_up", "review"]


class PRStatePayload(StrictPayload):
    previous_state: Literal["open", "draft", "closed", "merged", "unknown"] | None = None


class ReviewPublishedPayload(StrictPayload):
    finding_count: int = Field(ge=0)


class FindingSurfacedPayload(StrictPayload):
    severity: Literal["low", "medium", "high", "critical"]
    category: str = Field(min_length=1, max_length=100)


class FindingStatePayload(StrictPayload):
    previous_state: Literal["open", "resolved", "dismissed", "unknown"] | None = None


EventPayload = Annotated[
    RunStartedPayload
    | RunCompletedPayload
    | RunFailedPayload
    | RunCanceledPayload
    | RunCostRecordedPayload
    | FeedbackSubmittedPayload
    | FeedbackWithdrawnPayload
    | TaskMarkedCompletePayload
    | TaskAcceptedPayload
    | TaskReworkRequestedPayload
    | PROpenedPayload
    | PRObservedPayload
    | FindingObservedPayload
    | PRRunLinkedPayload
    | PRStatePayload
    | ReviewPublishedPayload
    | FindingSurfacedPayload
    | FindingStatePayload,
    Field(union_mode="left_to_right"),
]

_PAYLOAD_MODELS: dict[EventName, type[StrictPayload]] = {
    EventName.RUN_STARTED: RunStartedPayload,
    EventName.RUN_COMPLETED: RunCompletedPayload,
    EventName.RUN_FAILED: RunFailedPayload,
    EventName.RUN_CANCELED: RunCanceledPayload,
    EventName.RUN_COST_RECORDED: RunCostRecordedPayload,
    EventName.FEEDBACK_SUBMITTED: FeedbackSubmittedPayload,
    EventName.FEEDBACK_WITHDRAWN: FeedbackWithdrawnPayload,
    EventName.TASK_MARKED_COMPLETE: TaskMarkedCompletePayload,
    EventName.TASK_ACCEPTED: TaskAcceptedPayload,
    EventName.TASK_REWORK_REQUESTED: TaskReworkRequestedPayload,
    EventName.PR_OPENED: PROpenedPayload,
    EventName.PR_OBSERVED: PRObservedPayload,
    EventName.FINDING_OBSERVED: FindingObservedPayload,
    EventName.PR_RUN_LINKED: PRRunLinkedPayload,
    EventName.PR_MERGED: PRStatePayload,
    EventName.PR_CLOSED_WITHOUT_MERGE: PRStatePayload,
    EventName.PR_REOPENED: PRStatePayload,
    EventName.REVIEW_PUBLISHED: ReviewPublishedPayload,
    EventName.FINDING_SURFACED: FindingSurfacedPayload,
    EventName.FINDING_RESOLVED: FindingStatePayload,
    EventName.FINDING_DISMISSED: FindingStatePayload,
    EventName.FINDING_REOPENED: FindingStatePayload,
}


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID
    event_name: EventName
    schema_version: Literal[1] = SCHEMA_VERSION
    occurred_at: datetime
    recorded_at: datetime
    workspace_id: UUID
    environment: str = Field(min_length=1, max_length=100)
    producer: str = Field(min_length=1, max_length=100)
    producer_event_id: str = Field(min_length=1, max_length=500)
    source_version: int | None = Field(default=None, ge=0)
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    run_id: UUID | None = None
    preparation_run_id: UUID | None = None
    thread_id: UUID | None = None
    task_id: UUID | None = None
    pr_id: UUID | None = None
    review_id: UUID | None = None
    finding_id: UUID | None = None
    user_id: UUID | None = None
    team_id: UUID | None = None
    repository_id: UUID | None = None
    model_id: UUID | None = None
    entry_point: EntryPoint = EntryPoint.UNKNOWN
    privacy_classification: Literal["non_personal", "pseudonymous"]
    payload: EventPayload

    @model_validator(mode="before")
    @classmethod
    def validate_payload_model(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        event_name = EventName(value.get("event_name"))
        model = _PAYLOAD_MODELS[event_name]
        return {**value, "payload": model.model_validate(value.get("payload"))}

    @model_validator(mode="after")
    def validate_identity_and_time(self) -> EventEnvelope:
        expected = event_uuid(
            self.workspace_id,
            self.producer,
            self.producer_event_id,
            self.event_name,
            self.schema_version,
        )
        if self.event_id != expected:
            raise ValueError("event_id does not match deterministic event identity")
        if self.occurred_at.tzinfo is None or self.recorded_at.tzinfo is None:
            raise ValueError("event timestamps must be timezone-aware")
        _reject_forbidden_fields(self.payload.model_dump(mode="json"))
        return self


def _reject_forbidden_fields(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in _FORBIDDEN_KEYS:
                raise ValueError(f"forbidden analytics field: {key}")
            _reject_forbidden_fields(nested)
    elif isinstance(value, list | tuple):
        for nested in value:
            _reject_forbidden_fields(nested)


def deterministic_event_id(
    workspace_id: UUID,
    event_name: EventName,
    immutable_natural_key: str,
    source_version: str,
) -> UUID:
    return event_uuid(
        workspace_id,
        "open-swe",
        f"{immutable_natural_key}:{source_version}",
        event_name,
    )


def event_uuid(
    workspace_id: UUID,
    producer: str,
    producer_event_id: str,
    event_name: EventName,
    schema_version: int = SCHEMA_VERSION,
) -> UUID:
    return uuid5(
        EVENT_NAMESPACE,
        f"{workspace_id}:{producer}:{producer_event_id}:{event_name.value}:v{schema_version}",
    )


def person_uuid(workspace_id: UUID, provider: str, immutable_provider_id: str) -> UUID:
    return uuid5(PERSON_NAMESPACE, f"{workspace_id}:{provider}:{immutable_provider_id}")


def subject_uuid(workspace_id: UUID, subject_type: str, immutable_key: str) -> UUID:
    return uuid5(SUBJECT_NAMESPACE, f"{workspace_id}:{subject_type}:{immutable_key}")


def make_event(
    *,
    event_name: EventName,
    workspace_id: UUID,
    producer: str,
    producer_event_id: str,
    occurred_at: datetime,
    payload: StrictPayload,
    environment: str,
    **identifiers: object,
) -> EventEnvelope:
    recorded_at = datetime.now(UTC)
    return EventEnvelope.model_validate(
        {
            "event_id": event_uuid(workspace_id, producer, producer_event_id, event_name),
            "event_name": event_name,
            "occurred_at": occurred_at,
            "recorded_at": recorded_at,
            "workspace_id": workspace_id,
            "environment": environment,
            "producer": producer,
            "producer_event_id": producer_event_id,
            "privacy_classification": "pseudonymous"
            if identifiers.get("user_id")
            else "non_personal",
            "payload": payload,
            **identifiers,
        }
    )
