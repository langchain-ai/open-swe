"""LangSmith's audit envelope adapted to Open SWE identities."""

from datetime import UTC, datetime
from typing import Literal, TypedDict
from uuid import UUID, uuid7

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, field_serializer
from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database.orm import Base


class SettingsChange(TypedDict):
    before: bool | int | Literal["[REDACTED]"] | None
    after: bool | int | Literal["[REDACTED]"] | None


class AuditLogEnrichments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["http", "tool"] = "http"
    actor_kind: Literal["person", "api_key", "github_actions", "agent"] | None = None
    actor_login: str | None = None
    request_method: str | None = None
    request_path: str | None = None
    response_status_code: int | None = None
    resource_ids: list[str] = Field(default_factory=list)
    workspace: str | None = None
    thread_id: str | None = None
    delegated_from_sandbox_id: str | None = None
    channel_memory_patch: str | None = None
    proposed_by_slack_user_id: str | None = None
    approved_by_slack_user_id: str | None = None
    channel_memory_revision: int | None = None
    settings_scope: Literal["instance", "workspace"] | None = None
    settings_changes: dict[str, SettingsChange] | None = None

    @field_serializer("settings_changes")
    def _serialize_settings_changes(
        self, changes: dict[str, SettingsChange] | None
    ) -> (
        dict[str, dict[Literal["before", "after"], bool | int | Literal["[REDACTED]"] | None]]
        | None
    ):
        """Preserve null overrides even when the envelope excludes absent metadata."""
        if changes is None:
            return None
        return {
            field: {"before": change["before"], "after": change["after"]}
            for field, change in changes.items()
        }


class AuditLog(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid7)
    request_time: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    operation_name: str = Field(max_length=128)
    operation_succeeded: bool | None = None
    api_key_id: str | None = None
    user_id: UUID | None = None
    workspace_id: UUID | None = None
    enrichments: AuditLogEnrichments = Field(default_factory=AuditLogEnrichments)


class AuditLogRow(Base):
    __tablename__ = "audit_logs"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    request_time: Mapped[datetime]
    operation_name: Mapped[str] = mapped_column(String(128))
    operation_succeeded: Mapped[bool | None]
    api_key_id: Mapped[str | None]
    user_id: Mapped[UUID | None]
    workspace_id: Mapped[UUID | None]
    enrichments: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)


class AuditLogsCursor(BaseModel):
    request_time: AwareDatetime
    id: UUID


class AuditLogsPage(BaseModel):
    items: list[AuditLog]
    cursor: str | None
