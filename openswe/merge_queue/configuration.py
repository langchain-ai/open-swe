"""Operator-managed canary configuration stored on its owning workspace."""

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator
from sqlalchemy import select

from openswe.database import postgres
from openswe.merge_queue.admission import QueuePolicy, digest
from openswe.merge_queue.dispatch import WorkflowPolicy
from openswe.workspaces.rows import WorkspaceRow


class QueueConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_id: UUID
    repository: str = Field(pattern=r"^[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+$")
    repository_id: int = Field(gt=0, strict=True)
    mode: Literal["custom", "native"] = "custom"
    dispatch_url: HttpUrl | None = None
    workflow_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    sync_workflow_policy: bool = False
    workflow_policy_revision: int = Field(default=0, ge=0, strict=True)
    check_app_id: int | None = Field(default=None, gt=0, strict=True)
    operator_emails: frozenset[str] = Field(min_length=1)
    policy: QueuePolicy = Field(default_factory=lambda: QueuePolicy(checks={}))
    retention_days: int = Field(default=90, ge=7, le=365)

    @classmethod
    async def load(cls, workspace_id: UUID | None = None) -> Self | None:
        async with postgres.session() as session:
            query = select(WorkspaceRow.id, WorkspaceRow.merge_queue_config).where(
                WorkspaceRow.merge_queue_config.is_not(None)
            )
            if workspace_id is not None:
                query = query.where(WorkspaceRow.id == workspace_id)
            rows = (await session.execute(query.limit(2))).all()
        if not rows:
            return None
        if len(rows) != 1:
            raise ValueError("Only one canary workspace may configure a merge queue")
        workspace_id, config = rows[0]
        if not isinstance(config, dict):
            raise ValueError("Queue configuration must be a JSON object")
        return cls.model_validate({**config, "workspace_id": workspace_id})

    @property
    def generation(self) -> str:
        settings = self.model_dump(mode="json")
        if not self.sync_workflow_policy and self.workflow_policy_revision == 0:
            settings.pop("sync_workflow_policy")
            settings.pop("workflow_policy_revision")
        settings["operator_emails"] = sorted(self.operator_emails)
        return digest(settings)

    @property
    def native_trust(self) -> str:
        return digest(
            {
                **self.model_dump(
                    mode="json",
                    include={
                        "workspace_id",
                        "repository",
                        "repository_id",
                        "mode",
                        "dispatch_url",
                        "workflow_digest",
                        "check_app_id",
                    },
                ),
                **(
                    {
                        "sync_workflow_policy": self.sync_workflow_policy,
                        "workflow_policy_revision": self.workflow_policy_revision,
                    }
                    if self.sync_workflow_policy or self.workflow_policy_revision
                    else {}
                ),
                "checks": self.policy.model_dump(mode="json")["checks"],
                "dispatch_actor_id": self.policy.validation.dispatch_actor_id
                if self.policy.validation is not None
                else None,
            }
        )

    def with_workflow_policy(self, approved: WorkflowPolicy) -> Self:
        validation = self.policy.validation
        if (
            not self.sync_workflow_policy
            or validation is None
            or approved.repository.lower() != self.repository.lower()
            or approved.repository_id != self.repository_id
            or approved.dispatch_actor_id != validation.dispatch_actor_id
            or approved.check_app_id != self.check_app_id
        ):
            raise ValueError("Workflow policy does not match the configured queue identities")
        if approved.workflow_policy_revision < self.workflow_policy_revision or (
            approved.workflow_policy_revision == self.workflow_policy_revision
            and approved.workflow_digest != self.workflow_digest
        ):
            raise ValueError("Workflow policy revision is stale or was reused")
        return self.model_copy(
            update={
                "workflow_digest": approved.workflow_digest,
                "workflow_policy_revision": approved.workflow_policy_revision,
            }
        )

    @model_validator(mode="after")
    def validate_controller(self) -> Self:
        if self.sync_workflow_policy and (self.mode != "native" or self.check_app_id is None):
            raise ValueError("Workflow policy synchronization requires a native validation gate")
        if self.mode == "custom" and (
            self.policy.validation is None
            or not self.policy.checks
            or self.dispatch_url is None
            or self.workflow_digest is None
        ):
            raise ValueError("The queue needs validation and required-check provenance")
        if self.check_app_id is not None and (
            self.mode != "native"
            or self.policy.validation is None
            or self.dispatch_url is None
            or self.workflow_digest is None
        ):
            raise ValueError(
                "Native validation needs a trusted workflow, dispatcher and check issuer"
            )
        if any(
            not email.strip() or email != email.strip().lower() for email in self.operator_emails
        ):
            raise ValueError("Operator emails must be nonempty, lowercase, and trimmed")
        if self.dispatch_url is not None and (
            self.dispatch_url.scheme != "https"
            or self.dispatch_url.username
            or self.dispatch_url.password
            or self.dispatch_url.query
            or self.dispatch_url.fragment
        ):
            raise ValueError("Dispatch URL must use HTTPS without credentials, query or fragment")
        return self
