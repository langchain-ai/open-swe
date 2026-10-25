"""Strict GitHub snapshots used by queue admission."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Self

from pydantic import AliasPath, BaseModel, ConfigDict, Field, model_validator

GitSHA = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


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


class NativePull(Snapshot):
    number: int = Field(gt=0)
    head_sha: GitSHA = Field(validation_alias="headRefOid")
    state: str
    review_decision: str | None = Field(validation_alias="reviewDecision")
    draft: bool = Field(validation_alias="isDraft")
    branch: str = Field(validation_alias="baseRefName")
    repository: str | None = Field(validation_alias=AliasPath("headRepository", "nameWithOwner"))


class NativeEntry(Snapshot):
    id: str
    position: int = Field(gt=0)
    state: str
    enqueued_at: datetime = Field(validation_alias="enqueuedAt")
    pull: NativePull = Field(validation_alias="pullRequest")
    base_sha: GitSHA | None = Field(default=None, validation_alias=AliasPath("baseCommit", "oid"))
    head_sha: GitSHA | None = Field(default=None, validation_alias=AliasPath("headCommit", "oid"))
    parents: tuple[dict[str, GitSHA], ...] = Field(
        default=(), validation_alias=AliasPath("headCommit", "parents", "nodes")
    )
    parent_count: int = Field(
        default=0, validation_alias=AliasPath("headCommit", "parents", "totalCount")
    )


class NativeEntries(Snapshot):
    nodes: tuple[NativeEntry, ...]
    total_count: int = Field(validation_alias="totalCount")
    has_next: bool = Field(validation_alias=AliasPath("pageInfo", "hasNextPage"))

    @model_validator(mode="after")
    def complete(self) -> Self:
        if self.has_next or self.total_count != len(self.nodes):
            raise ValueError("Native queue exceeds the complete 100-entry observation limit")
        for values in (
            [entry.id for entry in self.nodes],
            [entry.position for entry in self.nodes],
            [entry.pull.number for entry in self.nodes],
        ):
            if len(set(values)) != len(values):
                raise ValueError("Native queue contains duplicate entries")
        return self


class NativeQueue(Snapshot):
    id: str
    entries: NativeEntries


@dataclass(frozen=True)
class NativeCandidate:
    head_sha: str
    base_sha: str
    members: tuple[NativePull, ...]


class NativeQueueSnapshot(Snapshot):
    repository_id: int = Field(gt=0, validation_alias="databaseId")
    repository: str = Field(validation_alias="nameWithOwner")
    branch: str = Field(validation_alias=AliasPath("ref", "name"))
    main_sha: GitSHA = Field(validation_alias=AliasPath("ref", "target", "oid"))
    queue: NativeQueue | None = Field(validation_alias="mergeQueue")
    observed_at: datetime | None = None

    @property
    def candidates(self) -> dict[str, NativeCandidate]:
        candidates: dict[str, NativeCandidate] = {}
        previous = self.main_sha
        members: list[NativePull] = []
        for entry in (
            sorted(self.queue.entries.nodes, key=lambda e: e.position) if self.queue else ()
        ):
            pull = entry.pull
            # GitHub can briefly return the old candidate after a head change or dequeue.
            if (
                pull.state != "OPEN"
                or pull.branch != self.branch
                or (pull.repository or "").lower() != self.repository.lower()
                or entry.head_sha is None
                or entry.head_sha in candidates
                or entry.base_sha != previous
                or entry.parent_count != 2
                or entry.parents != ({"oid": previous}, {"oid": pull.head_sha})
            ):
                break
            members.append(pull)
            candidates[entry.head_sha] = NativeCandidate(
                entry.head_sha, self.main_sha, tuple(members)
            )
            previous = entry.head_sha
        return candidates
