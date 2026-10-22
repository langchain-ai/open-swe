"""Strict GitHub snapshots used by queue admission."""

from datetime import datetime

from pydantic import AliasPath, BaseModel, ConfigDict, Field


class Snapshot(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)


class Review(Snapshot):
    id: int = Field(validation_alias="databaseId")
    state: str
    updated_at: datetime = Field(validation_alias="updatedAt")
    commit: str | None = Field(default=None, validation_alias=AliasPath("commit", "oid"))


class QueuePull(Snapshot):
    number: int
    state: str
    merged: bool
    draft: bool = Field(validation_alias="isDraft")
    head: str = Field(validation_alias="headRefOid")
    base: str = Field(validation_alias="baseRefOid")
    branch: str = Field(validation_alias="baseRefName")
    target: str = Field(validation_alias=AliasPath("baseRef", "target", "oid"))
    head_repository: str | None = Field(
        default=None, validation_alias=AliasPath("headRepository", "nameWithOwner")
    )
    base_repository: str = Field(validation_alias=AliasPath("baseRepository", "nameWithOwner"))
    decision: str | None = Field(validation_alias="reviewDecision")
    reviews: tuple[Review, ...] = ()


class Check(Snapshot):
    id: int
    name: str
    head_sha: str
    status: str
    conclusion: str | None
    started_at: datetime | None
    app_id: int = Field(validation_alias=AliasPath("app", "id"))
    suite_id: int = Field(validation_alias=AliasPath("check_suite", "id"))


class WorkflowPull(Snapshot):
    number: int
    head: str = Field(validation_alias=AliasPath("head", "sha"))


class WorkflowRun(Snapshot):
    id: int
    head_sha: str
    path: str
    event: str
    run_attempt: int
    check_suite_id: int
    repository: str = Field(validation_alias=AliasPath("repository", "full_name"))
    pull_requests: tuple[WorkflowPull, ...]


class Artifact(Snapshot):
    id: int
    name: str
    expired: bool


class ValidationRun(Snapshot):
    id: int = Field(gt=0)
    actor_id: int = Field(validation_alias=AliasPath("actor", "id"))
    triggering_actor_id: int = Field(validation_alias=AliasPath("triggering_actor", "id"))
    repository: str = Field(validation_alias=AliasPath("repository", "full_name"))
    head_repository: str = Field(validation_alias=AliasPath("head_repository", "full_name"))
    head_branch: str
    head_sha: str
    path: str
    event: str
    display_title: str
    run_attempt: int
    status: str
    conclusion: str | None


class ValidationJob(Snapshot):
    id: int = Field(gt=0)
    run_id: int = Field(gt=0)
    run_attempt: int
    name: str
    status: str
    conclusion: str | None


class ValidationRuns(Snapshot):
    total_count: int = Field(ge=0)
    workflow_runs: tuple[ValidationRun, ...]


class ValidationJobs(Snapshot):
    total_count: int = Field(ge=0)
    jobs: tuple[ValidationJob, ...]


class ValidationArtifacts(Snapshot):
    total_count: int = Field(ge=0)
    artifacts: tuple[Artifact, ...]


class ReviewPage(Snapshot):
    nodes: tuple[Review, ...]
    has_next: bool = Field(validation_alias=AliasPath("pageInfo", "hasNextPage"))
    cursor: str | None = Field(validation_alias=AliasPath("pageInfo", "endCursor"))


class CommitComparison(Snapshot):
    merge_base: str = Field(
        validation_alias=AliasPath("merge_base_commit", "sha"), pattern=r"^[0-9a-f]{40}$"
    )


class GitReference(Snapshot):
    ref: str
    sha: str = Field(validation_alias=AliasPath("object", "sha"), pattern=r"^[0-9a-f]{40}$")
