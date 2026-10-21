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


class Record(Base):
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

    queue_id: Mapped[UUID] = mapped_column(ForeignKey("merge_queue.id"))
    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey(PullRequest.id))
    request_key: Mapped[str]
    head_sha: Mapped[str]
    state: Mapped[RequestState] = mapped_column(Text, default="waiting_for_review")
    priority: Mapped[int] = mapped_column(default=0)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class Batch(Record):
    __tablename__ = "merge_queue_batch"

    queue_id: Mapped[UUID] = mapped_column(ForeignKey("merge_queue.id"))
    request_key: Mapped[str]
    base_sha: Mapped[str]
    policy: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    deadline: Mapped[datetime]
    max_attempts: Mapped[int]
    max_plans: Mapped[int]
    state: Mapped[Literal["active", "landed", "invalidated"]] = mapped_column(
        Text, default="active"
    )
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class BatchMember(Record):
    __tablename__ = "merge_queue_member"

    batch_id: Mapped[UUID] = mapped_column(ForeignKey(Batch.id, ondelete="CASCADE"))
    request_id: Mapped[UUID] = mapped_column(ForeignKey(MergeRequest.id))
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
