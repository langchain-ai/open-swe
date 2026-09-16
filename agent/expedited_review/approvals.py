"""Expedited approvals: a Slack vote on one revision of a small pull request.

An approval pins the pull request's head SHA and a fingerprint of its diff.
Votes belong to the approval, so a new commit voids them: the row is marked
``superseded`` and the agent has to ask again. One approval per pull request
may be active (``waiting``, ``open`` or ``merging``) at a time; a partial unique
index enforces that. A vote names its voter by ``users.id``, never by a GitHub
or Slack handle, so one person cannot vote twice under two identities.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal, Self
from uuid import UUID, uuid7

from sqlalchemy import BigInteger, ForeignKey, Text, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.github.pull_requests import PullRequest
from agent.github.repositories import Repository
from agent.users import User
from agent.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

ApprovalState = Literal["waiting", "open", "merging", "merged", "rejected", "superseded", "failed"]
VoteDecision = Literal["approve", "reject"]

ACTIVE_STATES: tuple[ApprovalState, ...] = ("waiting", "open", "merging")
REQUIRED_APPROVALS = 2


class ApprovalVote(Base):
    __tablename__ = "expedited_approval_vote"

    voter_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    approval_id: Mapped[UUID] = mapped_column(
        ForeignKey("expedited_approval.id", ondelete="CASCADE"), primary_key=True, init=False
    )
    decision: Mapped[VoteDecision] = mapped_column(Text, default="approve")
    github_review_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    feedback: Mapped[str] = mapped_column(server_default="", default="")
    voted_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    voter: Mapped[User] = relationship(init=False)

    @property
    def github_login(self) -> str:
        """The voter's GitHub handle for display; never an identity key."""
        return github_login_of(self.voter)


def github_login_of(user: User) -> str:
    login = next(
        (identity.login for identity in user.identities if identity.provider == "github"), ""
    )
    return login or user.display_name or str(user.id)[:8]


class ExpeditedApproval(Base):
    __tablename__ = "expedited_approval"

    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey("pull_request.id", ondelete="CASCADE"))
    head_sha: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    thread_id: Mapped[str] = mapped_column(server_default="", default="")
    diff_fingerprint: Mapped[str] = mapped_column(server_default="", default="")
    state: Mapped[ApprovalState] = mapped_column(Text, default="waiting")
    detail: Mapped[str] = mapped_column(server_default="", default="")
    slack_channel_id: Mapped[str] = mapped_column(server_default="", default="")
    slack_thread_ts: Mapped[str] = mapped_column(server_default="", default="")
    slack_message_ts: Mapped[str] = mapped_column(server_default="", default="")
    run_config: Mapped[JsonObject] = mapped_column(JSONB, default_factory=dict)
    cron_id: Mapped[str] = mapped_column(server_default="", default="")
    # Checks failing that GitHub does not require, so the card can keep naming
    # them across re-renders that have no readiness pass of their own.
    advisory_failures: Mapped[list[str]] = mapped_column(JSONB, default_factory=list)
    votes: Mapped[list[ApprovalVote]] = relationship(
        default_factory=list, cascade="all, delete-orphan", order_by=lambda: ApprovalVote.voted_at
    )
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    pull_request: Mapped[PullRequest] = relationship(init=False)

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES

    @property
    def approvals(self) -> list[ApprovalVote]:
        return [vote for vote in self.votes if vote.decision == "approve"]

    @property
    def approvers(self) -> list[str]:
        """GitHub handles of the approvers, for display."""
        return [vote.github_login for vote in self.approvals]

    @property
    def rejection(self) -> ApprovalVote | None:
        return next((vote for vote in self.votes if vote.decision == "reject"), None)

    def vote_by(self, user_id: UUID) -> ApprovalVote | None:
        return next((vote for vote in self.votes if vote.voter_user_id == user_id), None)

    @property
    def slack_location(self) -> tuple[str, str] | None:
        if self.slack_channel_id and self.slack_thread_ts:
            return self.slack_channel_id, self.slack_thread_ts
        return None

    @classmethod
    def _loaded(cls, statement):  # noqa: ANN001, ANN206
        return statement.options(
            selectinload(cls.votes).selectinload(ApprovalVote.voter).selectinload(User.identities),
            selectinload(cls.pull_request),
        )

    @classmethod
    async def get(cls, approval_id: UUID) -> Self | None:
        async with postgres.session() as session:
            return await session.scalar(cls._loaded(select(cls)).where(cls.id == approval_id))

    @classmethod
    async def active_for(cls, owner: str, repo: str, number: int) -> Self | None:
        async with postgres.session() as session:
            return await session.scalar(
                cls._loaded(select(cls))
                .join(cls.pull_request)
                .join(PullRequest.repository)
                .where(
                    Repository.key == f"{owner}/{repo}".lower(),
                    PullRequest.number == number,
                    cls.state.in_(ACTIVE_STATES),
                )
            )

    @classmethod
    async def all_for_repo(cls, owner: str, repo: str) -> list[Self]:
        """Every approval a repository has ever had, oldest first."""
        async with postgres.session() as session:
            rows = await session.scalars(
                cls._loaded(select(cls))
                .join(cls.pull_request)
                .join(PullRequest.repository)
                .where(Repository.key == f"{owner}/{repo}".lower())
                .order_by(cls.created_at, cls.id)
            )
            return list(rows)

    @classmethod
    async def active_in_repo(cls, owner: str, repo: str) -> list[Self]:
        async with postgres.session() as session:
            rows = await session.scalars(
                cls._loaded(select(cls))
                .join(cls.pull_request)
                .join(PullRequest.repository)
                .where(Repository.key == f"{owner}/{repo}".lower(), cls.state.in_(ACTIVE_STATES))
                .order_by(cls.created_at)
            )
            return list(rows)

    async def save(self) -> Self:
        cls = type(self)
        async with postgres.session() as session:
            session.add(self)
            await session.flush()
            stored = await session.scalar(
                cls._loaded(select(cls))
                .where(cls.id == self.id)
                .execution_options(populate_existing=True)
            )
        if stored is None:
            raise RuntimeError(f"expedited approval {self.id} vanished during save")
        return stored

    @classmethod
    @asynccontextmanager
    async def locked(cls, approval_id: UUID) -> AsyncIterator[tuple[AsyncSession, Self | None]]:
        """The row locked for update; changes made to it commit when the block exits."""
        async with postgres.session() as session:
            row = await session.scalar(
                cls._loaded(select(cls))
                .where(cls.id == approval_id)
                .with_for_update(of=cls)
                .execution_options(populate_existing=True)
            )
            yield session, row
