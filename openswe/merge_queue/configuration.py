"""Operator-managed canary configuration stored on its owning workspace."""

from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator
from sqlalchemy import select

from openswe.config import ENV
from openswe.database import postgres
from openswe.merge_queue.admission import QueuePolicy
from openswe.workspaces.rows import WorkspaceRow


class QueueConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_id: UUID
    repository: str = Field(pattern=r"^[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+$")
    repository_id: int = Field(gt=0, strict=True)
    dispatch_url: HttpUrl
    workflow_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy: QueuePolicy
    retention_days: int = Field(default=90, ge=7, le=365)

    @classmethod
    async def load(cls) -> Self | None:
        async with postgres.session() as session:
            rows = (
                await session.execute(
                    select(WorkspaceRow.id, WorkspaceRow.merge_queue_config)
                    .where(WorkspaceRow.merge_queue_config.is_not(None))
                    .limit(2)
                )
            ).all()
        if not rows:
            return None
        if len(rows) != 1:
            raise ValueError("Only one canary workspace may configure a merge queue")
        workspace_id, config = rows[0]
        if not isinstance(config, dict):
            raise ValueError("Queue configuration must be a JSON object")
        return cls.model_validate({**config, "workspace_id": workspace_id})

    @model_validator(mode="after")
    def validate_controller(self) -> Self:
        if ENV.SANDBOX_TYPE.get() == "local":
            raise ValueError("The queue requires isolated agent sandboxes")
        if self.policy.validation is None or not self.policy.checks:
            raise ValueError("The queue needs validation and required-check provenance")
        if (
            self.dispatch_url.scheme != "https"
            or self.dispatch_url.username
            or self.dispatch_url.password
            or self.dispatch_url.query
            or self.dispatch_url.fragment
        ):
            raise ValueError("Dispatch URL must use HTTPS without credentials, query or fragment")
        return self
