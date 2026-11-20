"""Operator-managed canary configuration stored on its repository binding."""

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openswe.database import postgres
from openswe.merge_queue.admission import QueuePolicy, digest
from openswe.merge_queue.dispatch import WorkflowPolicy
from openswe.merge_queue.recovery import RecoverySettings
from openswe.routing.store import RepositoryBinding


class QueueConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    routing_id: UUID
    repository: str = Field(pattern=r"^[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+$")
    repository_id: int = Field(gt=0, strict=True)
    mode: Literal["custom", "native"] = "custom"
    dispatch_url: HttpUrl | None = None
    workflow_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    sync_workflow_policy: bool = False
    workflow_policy_revision: int = Field(default=0, ge=0, strict=True)
    allow_main_advance: bool = Field(default=False, exclude_if=lambda value: not value)
    allow_test_exceptions: bool = Field(default=False, exclude_if=lambda value: not value)
    check_app_id: int | None = Field(default=None, gt=0, strict=True)
    operator_emails: frozenset[str] = Field(min_length=1)
    policy: QueuePolicy = Field(default_factory=lambda: QueuePolicy(checks={}))
    retention_days: int = Field(default=90, ge=7, le=365)
    recovery: RecoverySettings | None = None

    @classmethod
    async def load(
        cls, routing_id: UUID | None = None, *, session: AsyncSession | None = None
    ) -> Self | None:
        async with postgres.session(session) as session:
            query = select(RepositoryBinding.id, RepositoryBinding.merge_queue_config).where(
                RepositoryBinding.merge_queue_config.is_not(None)
            )
            if routing_id is not None:
                query = query.where(RepositoryBinding.id == routing_id)
            rows = (await session.execute(query.limit(2))).all()
        if not rows:
            return None
        if len(rows) != 1:
            raise ValueError("Only one canary repository may configure a merge queue")
        routing_id, config = rows[0]
        if not isinstance(config, dict):
            raise ValueError("Queue configuration must be a JSON object")
        return cls.model_validate({**config, "routing_id": routing_id})

    @property
    def generation(self) -> str:
        settings = self.model_dump(mode="json")
        if self.recovery is None:
            settings.pop("recovery")
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
                        "routing_id",
                        "repository",
                        "repository_id",
                        "mode",
                        "dispatch_url",
                        "workflow_digest",
                        "check_app_id",
                        "allow_main_advance",
                        "allow_test_exceptions",
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
                **(
                    {"validation_suite": self.policy.validation.suite}
                    if self.policy.validation is not None
                    and self.policy.validation.suite != "smoke"
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
            and (
                approved.workflow_digest != self.workflow_digest
                or approved.validation_suite != validation.suite
                or approved.allow_main_advance != self.allow_main_advance
                or approved.allow_test_exceptions != self.allow_test_exceptions
            )
        ):
            raise ValueError("Workflow policy revision is stale or was reused")
        return self.model_validate(
            {
                **self.model_dump(),
                "workflow_digest": approved.workflow_digest,
                "workflow_policy_revision": approved.workflow_policy_revision,
                "allow_main_advance": approved.allow_main_advance,
                "allow_test_exceptions": approved.allow_test_exceptions,
                "policy": {
                    **self.policy.model_dump(),
                    "validation": {**validation.model_dump(), "suite": approved.validation_suite},
                },
            }
        )

    @model_validator(mode="after")
    def validate_controller(self) -> Self:
        if self.allow_test_exceptions and (not self.sync_workflow_policy or self.recovery is None):
            raise ValueError("Test exceptions require synchronized policy and recovery")
        if self.allow_main_advance and not self.sync_workflow_policy:
            raise ValueError("Main advancement requires synchronized gateway policy")
        if self.recovery is not None and (self.mode != "native" or self.check_app_id is None):
            raise ValueError("Automatic recovery requires a native validation gate")
        if self.sync_workflow_policy and (self.mode != "native" or self.check_app_id is None):
            raise ValueError("Workflow policy synchronization requires a native validation gate")
        if self.mode == "custom" and (
            self.policy.validation is None
            or not self.policy.checks
            or self.dispatch_url is None
            or self.workflow_digest is None
            or self.policy.validation.suite != "smoke"
        ):
            raise ValueError(
                "The custom queue needs smoke validation and required-check provenance"
            )
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
