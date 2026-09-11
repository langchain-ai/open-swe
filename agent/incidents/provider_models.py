"""Provider bindings and operation history outlive operational incident data."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from agent.store import now_iso


class ProviderValue(BaseModel):
    id: str = ""
    name: str = ""


class ProviderSnapshot(BaseModel):
    external_id: str
    title: str = ""
    url: str = ""
    status: ProviderValue = Field(default_factory=ProviderValue)
    severity: ProviderValue = Field(default_factory=ProviderValue)
    slack_channel_id: str = ""
    postmortem: str = ""
    resolved_at: str | None = None


class ProviderBinding(BaseModel):
    incident_id: str
    workspace_id: str
    channel_id: str
    provider: Literal["incident_io"] = "incident_io"
    connection_name: str
    external_id: str
    url: str = ""
    snapshot: ProviderSnapshot | None = None
    snapshot_scope: str = ""
    last_synced_at: str | None = None
    error: str | None = None
    error_kind: str | None = None


class ProviderOperation(BaseModel):
    id: str
    incident_id: str
    action: str
    content_hash: str = ""
    connection_name: str = ""
    external_id: str = ""
    updates: dict[str, str] = Field(default_factory=dict)
    status: Literal["accepted", "sending", "succeeded", "failed", "unknown"] = "accepted"
    error: str | None = None
    actor: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
