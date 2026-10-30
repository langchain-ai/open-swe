"""Queue history. Membership and evidence are snapshots, not live PR metadata."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid7

from pydantic import JsonValue
from sqlalchemy import BigInteger, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database.orm import NOW, Base
from openswe.github.pull_requests import PullRequest
from openswe.users.models import User

RequestState = Literal["waiting_for_review", "queued", "batched", "merged", "withdrawn"]
AttemptResult = Literal["succeeded", "failed", "timed_out", "cancelled"]


@dataclass(frozen=True)
class Lease:
    owner: UUID
    fence: int
    configuration_generation: str | None = None


class Record(Base, kw_only=True):
    __abstract__ = True

    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )


class MergeRequest(Record):
    __tablename__ = "merge_queue_request"

    queue_id: Mapped[UUID] = mapped_column(ForeignKey("merge_queue.id", ondelete="CASCADE"))
    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey(PullRequest.id))
    request_key: Mapped[str]
    head_sha: Mapped[str]
    state: Mapped[RequestState] = mapped_column(Text, default="waiting_for_review")
    priority: Mapped[int] = mapped_column(default=0)
    dependencies: Mapped[list[int]] = mapped_column(JSONB, default_factory=list)
    admission: Mapped[dict[str, JsonValue] | None] = mapped_column(
        JSONB(none_as_null=True), default=None
    )
    blocked_reason: Mapped[str | None] = mapped_column(default=None)
    blocked_base_sha: Mapped[str | None] = mapped_column(default=None)
    queued_at: Mapped[datetime | None] = mapped_column(default=None)
    last_checked_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class Batch(Record):
    __tablename__ = "merge_queue_batch"

    queue_id: Mapped[UUID] = mapped_column(ForeignKey("merge_queue.id", ondelete="CASCADE"))
    request_key: Mapped[str]
    base_sha: Mapped[str]
    policy: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    deadline: Mapped[datetime]
    max_attempts: Mapped[int]
    max_plans: Mapped[int]
    candidate: Mapped[dict[str, JsonValue] | None] = mapped_column(
        JSONB(none_as_null=True), default=None
    )
    build_attempts: Mapped[int] = mapped_column(default=0)
    candidate_error: Mapped[str | None] = mapped_column(default=None)
    published_at: Mapped[datetime | None] = mapped_column(default=None)
    ref_deleted_at: Mapped[datetime | None] = mapped_column(default=None)
    state: Mapped[Literal["active", "landed", "invalidated"]] = mapped_column(
        Text, default="active"
    )
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class BatchMember(Record):
    __tablename__ = "merge_queue_member"

    batch_id: Mapped[UUID] = mapped_column(ForeignKey(Batch.id, ondelete="CASCADE"))
    request_id: Mapped[UUID] = mapped_column(ForeignKey(MergeRequest.id, ondelete="CASCADE"))
    position: Mapped[int]
    head_sha: Mapped[str]


class Attempt(Record):
    __tablename__ = "merge_queue_attempt"

    batch_id: Mapped[UUID] = mapped_column(ForeignKey(Batch.id, ondelete="CASCADE"))
    request_key: Mapped[str]
    number: Mapped[int]
    deadline: Mapped[datetime]
    state: Mapped[Literal["running", "succeeded", "failed", "timed_out", "cancelled"]] = (
        mapped_column(Text, default="running")
    )
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class Evidence(Record):
    __tablename__ = "merge_queue_evidence"

    attempt_id: Mapped[UUID] = mapped_column(ForeignKey(Attempt.id, ondelete="CASCADE"))
    key: Mapped[str]
    reference: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)


class Decision(Record):
    __tablename__ = "merge_queue_decision"

    batch_id: Mapped[UUID] = mapped_column(ForeignKey(Batch.id, ondelete="CASCADE"))
    request_key: Mapped[str]
    plan: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    lease_owner: Mapped[UUID]
    fence: Mapped[int] = mapped_column(BigInteger)
