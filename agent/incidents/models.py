"""Validated records shared by incident channels, agent tools, and the dashboard."""

import re
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from agent.store import now_iso


class IncidentPolicy(BaseModel):
    enabled: bool = False
    workspace_id: str = ""
    slack_app_id: str = ""
    channel_prefix: str = "inc-"
    excluded_channel_ids: list[str] = Field(default_factory=list)
    model: str | None = None
    max_model_calls: int = Field(default=20, ge=1, le=20)
    version: int = 0
    enabled_at: float = 0

    @field_validator("channel_prefix")
    @classmethod
    def validate_prefix(cls, value: str) -> str:
        value = value.strip().removeprefix("#").lower()
        if not re.fullmatch(r"[a-z0-9_-]{1,60}", value):
            raise ValueError("Use a nonempty Slack channel prefix, such as inc-")
        return value


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


class IncidentReport(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    summary: str
    impact: str = ""
    next_steps: list[str] = Field(default_factory=list)
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


IncidentStatus = Literal["watching", "paused", "needs_attention", "completed"]


class Incident(BaseModel):
    """One enrolled Slack channel and its system-owned agent thread."""

    id: str
    workspace_id: str
    channel_id: str
    channel_name: str = ""
    title: str = ""
    thread_id: str = ""
    anchor_ts: str | None = None
    status: IncidentStatus = "watching"
    reason: str = ""
    is_archived: bool = False
    last_failure_run_id: str = ""
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    activity: list[Activity] = Field(default_factory=list)


class IncidentReportRecord(BaseModel):
    """The latest report the agent recorded; written only by the report tool."""

    incident_id: str
    report: IncidentReport
    digest: str
    run_id: str = ""
    updated_at: str = Field(default_factory=now_iso)
    activity: list[Activity] = Field(default_factory=list)
