"""Review guide sessions and the changed lines each person has approved.

A session is one code channel walking one person through one pull request,
along with where the walkthrough of its current head stands. What they approved is kept per
person and pull request, keyed by line content rather than commit, so a rebase
or force-push never asks them to reread it.
"""

from collections import Counter
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from sqlalchemy import ForeignKey, Text, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.github.pull_requests import PullRequest
from agent.github.repositories import Repository
from agent.review_guide.walk import Walk
from agent.utils.json_types import JsonObject

ASSISTANT_ID = "review-guide"

# A reviewer ends by approving; the author, who cannot approve their own PR, ends by readying it.
GuideMode = Literal["reviewer", "author"]


class ReviewGuideSeenLine(Base):
    __tablename__ = "review_guide_seen_line"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    pull_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("pull_request.id", ondelete="CASCADE"), primary_key=True
    )
    line_key: Mapped[str] = mapped_column(primary_key=True)
    seen_count: Mapped[int]


class ReviewGuideSession(Base):
    __tablename__ = "review_guide_session"

    thread_id: Mapped[str] = mapped_column(primary_key=True)
    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey("pull_request.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    slack_channel_id: Mapped[str]
    workspace_slug: Mapped[str | None] = mapped_column(default=None)
    mode: Mapped[GuideMode] = mapped_column(Text, default="reviewer")
    walk_json: Mapped[JsonObject | None] = mapped_column("walkthrough", JSONB, default=None)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    closed_at: Mapped[datetime | None] = mapped_column(default=None, init=False)
    pull_request: Mapped[PullRequest] = relationship(init=False)

    @property
    def closed(self) -> bool:
        return self.closed_at is not None

    async def set_closed(self, closed: bool) -> None:
        """Close the session so nothing but a person's message wakes it, or reopen it."""
        async with postgres.session() as session:
            self.closed_at = await session.scalar(
                update(ReviewGuideSession)
                .where(ReviewGuideSession.thread_id == self.thread_id)
                .values(closed_at=func.clock_timestamp() if closed else None)
                .returning(ReviewGuideSession.closed_at)
            )
            await session.commit()

    @classmethod
    async def for_channel(cls, channel_id: str) -> Self | None:
        if not postgres.configured():
            return None
        async with postgres.session() as session:
            return await session.scalar(
                select(cls)
                .options(selectinload(cls.pull_request))
                .where(cls.slack_channel_id == channel_id)
            )

    @property
    def walk(self) -> Walk | None:
        return Walk.model_validate(self.walk_json) if self.walk_json else None

    async def save_walk(self, walk: Walk | None) -> None:
        self.walk_json = walk.model_dump(mode="json") if walk else None
        async with postgres.session() as session:
            await session.execute(
                update(ReviewGuideSession)
                .where(ReviewGuideSession.thread_id == self.thread_id)
                .values(walk_json=self.walk_json)
            )
            await session.commit()

    @classmethod
    async def get(cls, thread_id: str) -> Self | None:
        if not postgres.configured():
            return None
        async with postgres.session() as session:
            return await session.scalar(
                select(cls)
                .options(selectinload(cls.pull_request))
                .where(cls.thread_id == thread_id)
            )

    @classmethod
    async def exists(cls, thread_id: str) -> bool:
        return await cls.get(thread_id) is not None

    @classmethod
    async def for_pull_request(cls, owner: str, repo: str, number: int) -> list[Self]:
        async with postgres.session() as session:
            rows = await session.scalars(
                select(cls)
                .options(selectinload(cls.pull_request))
                .join(cls.pull_request)
                .join(PullRequest.repository)
                .where(Repository.key == f"{owner}/{repo}".lower(), PullRequest.number == number)
            )
            return list(rows)

    @classmethod
    async def author_threads(cls, pull_request_id: UUID) -> list[str]:
        """The author's own guide sessions on a PR, oldest first."""
        async with postgres.session() as session:
            rows = await session.scalars(
                select(cls.thread_id)
                .where(cls.pull_request_id == pull_request_id, cls.mode == "author")
                .order_by(cls.created_at)
            )
            return list(rows)

    @classmethod
    async def create(
        cls,
        *,
        thread_id: str,
        pull_request: PullRequest,
        user_id: UUID,
        slack_channel_id: str,
        workspace_slug: str | None,
        mode: GuideMode,
    ) -> None:
        async with postgres.session() as session:
            session.add(
                cls(
                    thread_id=thread_id,
                    pull_request_id=pull_request.id,
                    user_id=user_id,
                    slack_channel_id=slack_channel_id,
                    workspace_slug=workspace_slug,
                    mode=mode,
                )
            )
            await session.commit()

    async def seen_lines(self) -> Counter[str]:
        """How many times this person has approved each line key on this pull request."""
        async with postgres.session() as session:
            rows = await session.execute(
                select(ReviewGuideSeenLine.line_key, ReviewGuideSeenLine.seen_count).where(
                    ReviewGuideSeenLine.user_id == self.user_id,
                    ReviewGuideSeenLine.pull_request_id == self.pull_request_id,
                )
            )
            return Counter(dict(rows.tuples().all()))

    async def mark_seen(self, lines: Counter[str]) -> None:
        if not lines:
            return
        upsert = insert(ReviewGuideSeenLine).values(
            [
                {
                    "user_id": self.user_id,
                    "pull_request_id": self.pull_request_id,
                    "line_key": key,
                    "seen_count": count,
                }
                for key, count in lines.items()
            ]
        )
        async with postgres.session() as session:
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[
                        ReviewGuideSeenLine.user_id,
                        ReviewGuideSeenLine.pull_request_id,
                        ReviewGuideSeenLine.line_key,
                    ],
                    set_={
                        "seen_count": ReviewGuideSeenLine.seen_count + upsert.excluded.seen_count
                    },
                )
            )
            await session.commit()
