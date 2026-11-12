"""Immutable workflow inputs, bound to the suite selected by the controller policy."""

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openswe.merge_queue.git import SHA

WORKFLOW = "merge_queue_validate.yml"
SERVICE_JOBS = ("candidate-smoke", "candidate-services / service-results")


class ValidationPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    dispatch_actor_id: int = Field(gt=0, strict=True)
    deadline_seconds: int = Field(default=1800, ge=60, le=86400)
    suite: Literal["smoke", "services"] = Field(
        default="smoke", exclude_if=lambda value: value == "smoke"
    )


class ValidationRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: Literal[1, 2] = 1
    repository: str
    batch_id: UUID
    attempt_id: UUID
    candidate_sha: SHA
    workflow_sha: SHA
    expected_jobs: tuple[str, ...] = ("candidate-smoke",)
    base_sha: SHA | None = Field(default=None, exclude_if=lambda value: value is None)
    dispatch_actor_id: int = Field(gt=0, strict=True)

    @model_validator(mode="after")
    def require_suite(self) -> Self:
        expected = SERVICE_JOBS if self.version == 2 else ("candidate-smoke",)
        if self.expected_jobs != expected or (
            self.base_sha != self.workflow_sha if self.version == 2 else self.base_sha is not None
        ):
            raise ValueError("Validation suite or batch base does not match the contract")
        return self

    @property
    def title(self) -> str:
        return f"merge-queue {self.batch_id} {self.attempt_id} {self.candidate_sha}"


class ValidationDispatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    contract: ValidationRequest
    policy_digest: str
    lease_owner: UUID
    fence: int
