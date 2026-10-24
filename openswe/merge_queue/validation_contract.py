"""The smoke workflow's wire contract; success is not permission to promote."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from openswe.merge_queue.git import SHA

WORKFLOW = "merge_queue_validate.yml"


class ValidationPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    dispatch_actor_id: int = Field(gt=0, strict=True)
    deadline_seconds: int = Field(default=1800, ge=60, le=86400)


class ValidationRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: Literal[1] = 1
    repository: str
    batch_id: UUID
    attempt_id: UUID
    candidate_sha: SHA
    workflow_sha: SHA
    expected_jobs: tuple[Literal["candidate-smoke"]] = ("candidate-smoke",)
    dispatch_actor_id: int = Field(gt=0, strict=True)

    @property
    def title(self) -> str:
        return f"merge-queue {self.batch_id} {self.attempt_id} {self.candidate_sha}"


class ValidationDispatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    contract: ValidationRequest
    policy_digest: str
    lease_owner: UUID
    fence: int
