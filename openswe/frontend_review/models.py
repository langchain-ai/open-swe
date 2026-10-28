from datetime import datetime
from uuid import UUID, uuid7

from pydantic import JsonValue
from sqlalchemy import ForeignKey, LargeBinary, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from openswe.database.orm import NOW, Base
from openswe.github.pull_requests import PullRequest
from openswe.users.models import User

from .schemas import AuthMode, MediaType, RunStatus


class EvidenceRecord(Base, kw_only=True):
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


class FrontendReviewRun(EvidenceRecord):
    __tablename__ = "frontend_review_run"

    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey(PullRequest.id, ondelete="CASCADE"))
    base_sha: Mapped[str]
    head_sha: Mapped[str]
    base_url: Mapped[str]
    head_url: Mapped[str]
    paths: Mapped[list[str]] = mapped_column(JSONB)
    auth_mode: Mapped[AuthMode] = mapped_column(Text)
    status: Mapped[RunStatus] = mapped_column(Text, default="pending")
    journeys: Mapped[list[dict[str, JsonValue]]] = mapped_column(JSONB, default_factory=list)
    error: Mapped[str | None] = mapped_column(default=None)
    report: Mapped[dict[str, JsonValue] | None] = mapped_column(
        JSONB(none_as_null=True), default=None
    )
    pull_request: Mapped[PullRequest] = relationship(init=False, lazy="selectin")

    @property
    def owner(self) -> str:
        return self.pull_request.owner

    @property
    def repo(self) -> str:
        return self.pull_request.repo

    @property
    def pr_number(self) -> int:
        return self.pull_request.number


class FrontendReviewMedia(EvidenceRecord):
    __tablename__ = "frontend_review_media"
    __table_args__ = (UniqueConstraint("run_id", "name"),)

    run_id: Mapped[UUID] = mapped_column(ForeignKey(FrontendReviewRun.id, ondelete="CASCADE"))
    name: Mapped[str]
    content_type: Mapped[MediaType] = mapped_column(Text)
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
