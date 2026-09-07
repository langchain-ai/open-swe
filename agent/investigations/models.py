"""Validated records shared by investigation workers, tools, and dashboard."""

import re
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from agent.store import now_iso


class InvestigationPolicy(BaseModel):
    enabled: bool = False
    workspace_id: str = ""
    slack_app_id: str = ""
    channel_prefix: str = "inc-"
    excluded_channel_ids: list[str] = Field(default_factory=list)
    model: str | None = None
    max_model_calls: int = Field(default=20, ge=1, le=20)
    max_pass_seconds: int = Field(default=300, ge=10, le=300)
    idle_timeout_seconds: int = Field(default=7200, ge=60, le=86400)
    max_watch_seconds: int = Field(default=86400, ge=60, le=86400)
    version: int = 0
    enabled_at: float = 0

    @field_validator("channel_prefix")
    @classmethod
    def validate_prefix(cls, value: str) -> str:
        value = value.strip().removeprefix("#").lower()
        if not re.fullmatch(r"[a-z0-9_-]{1,60}", value):
            raise ValueError("Use a nonempty Slack channel prefix, such as inc-")
        return value


class InvestigationMessage(BaseModel):
    id: str = ""
    ts: str = ""
    thread_ts: str = ""
    user: str = ""
    text: str = ""
    bot_id: str = ""
    app_id: str = ""
    event_type: str = ""
    deleted: bool = False
    edited_at: str = ""
    source_url: str = ""


class Evidence(BaseModel):
    id: str
    source: str
    url: str = ""
    summary: str
    query: str | None = None
    retrieved_at: str = Field(default_factory=now_iso)


class Hypothesis(BaseModel):
    title: str
    assessment: Literal["supported", "plausible", "rejected"] = "plausible"
    evidence_ids: list[str] = Field(default_factory=list)


class InvestigationReport(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    summary: str
    impact: str = ""
    outcome: Literal["inconclusive", "findings"] = "inconclusive"
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    checked: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)


class Activity(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    type: str
    at: str = Field(default_factory=now_iso)
    summary: str


class PendingRequest(BaseModel):
    id: str
    text: str = ""
    thread_ts: str | None = None


class PendingPublication(BaseModel):
    reason: str
    text: str
    thread_ts: str | None = None
    key: str
    policy_version: int


class Investigation(BaseModel):
    id: str
    workspace_id: str
    channel_id: str
    channel_name: str = ""
    title: str = ""
    thread_id: str
    anchor_ts: str | None = None
    status: Literal[
        "pending", "investigating", "watching", "paused", "needs_attention", "completed"
    ] = "pending"
    reason: str = ""
    is_archived: bool = False
    can_read: bool = False
    joined: bool = False
    bootstrap_complete: bool = False
    expired: bool = False
    last_verified_at: float = 0
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    watch_started_at: float = 0
    last_source_activity_at: float = 0
    last_published_at: float = 0
    last_published_digest: str = ""
    last_context_hash: str = ""
    pending_since: float = 0
    retry_after: float = 0
    setup_attempts: int = 0
    report: InvestigationReport | None = None
    messages: list[InvestigationMessage] = Field(default_factory=list)
    activity: list[Activity] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    processed_receipts: list[str] = Field(default_factory=list)
    pending_requests: list[PendingRequest] = Field(default_factory=list)
    pending_publications: list[PendingPublication] = Field(default_factory=list)
    pass_return_status: Literal["watching", "paused", "completed"] | None = None
    pass_return_reason: str = ""
    last_control_at: float = 0
    last_control_priority: int = 0


class Receipt(BaseModel):
    id: str
    workspace_id: str
    channel_id: str = ""
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    actor: dict[str, Any] = Field(default_factory=dict)
    received_at: float
    source_time: float = 0
    content_hash: str = ""


class Publication(BaseModel):
    id: str
    investigation_id: str
    text: str
    thread_ts: str | None = None
    reason: str
    status: Literal["pending", "sending", "sent", "unknown", "failed"] = "pending"
    slack_message_ts: str | None = None
    created_at: float


class CoordinatorState(BaseModel):
    active_thread_id: str | None = None
    active_since: float = 0
    last_operation: dict[str, Any] | None = None
