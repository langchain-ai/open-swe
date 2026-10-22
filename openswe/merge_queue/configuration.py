"""Deployment-owned canary configuration; never supplied by a job or API caller."""

from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openswe.config import ENV
from openswe.merge_queue.admission import QueuePolicy


class QueueConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_id: UUID
    repository: str = Field(pattern=r"^[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+$")
    repository_id: int = Field(gt=0, strict=True)
    app_id: int = Field(gt=0, strict=True)
    installation_id: int = Field(gt=0, strict=True)
    workflow_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    operator_emails: frozenset[str] = Field(min_length=1)
    policy: QueuePolicy
    retention_days: int = Field(default=90, ge=7, le=365)

    @model_validator(mode="after")
    def validate_controller(self) -> Self:
        if ENV.SANDBOX_TYPE.get() == "local":
            raise ValueError("The queue requires isolated agent sandboxes")
        if self.policy.validation is None or not self.policy.checks:
            raise ValueError("The queue needs validation and required-check provenance")
        if any(
            not email.strip() or email != email.strip().lower() for email in self.operator_emails
        ):
            raise ValueError("Operator emails must be nonempty, lowercase, and trimmed")
        if self.app_id == ENV.GITHUB_APP_ID.get_int(0):
            raise ValueError("The queue must use a separate GitHub App from agent sandboxes")
        return self


def load_configuration() -> QueueConfiguration | None:
    raw = ENV.MERGE_QUEUE_CONFIG.optional()
    return QueueConfiguration.model_validate_json(raw) if raw else None
