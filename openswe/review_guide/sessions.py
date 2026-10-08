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

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.pull_requests import PullRequest
from openswe.github.repositories import Repository
from openswe.review_guide.walk import Walk
from openswe.users.models import User
from openswe.utils.json_types import JsonObject

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
    # sha256 of (path, sign, text): the line by content, so it stays seen across rebases.
    line_key: Mapped[str] = mapped_column(primary_key=True)
    # Copies of that exact line approved; identical lines, such as a lone `}`, each count once.
    seen_count: Mapped[int]


class ReviewGuideSession(Base):
    __tablename__ = "review_guide_session"

    # The LangGraph thread, which is also the code channel's session id.
    thread_id: Mapped[str] = mapped_column(primary_key=True)
    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey("pull_request.id", ondelete="CASCADE"))
    # The reader being walked through.
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    slack_channel_id: Mapped[str]
    # Sandbox workspace the guide boots from; None is the default image.
    workspace_slug: Mapped[str | None] = mapped_column(default=None)
    mode: Mapped[GuideMode] = mapped_column(Text, default="reviewer")
    # A serialized `Walk` for the head it was built at.
    walk_json: Mapped[JsonObject | None] = mapped_column("walkthrough", JSONB, default=None)
    # Slack ts of the progress message edited in place; empty before it is posted.
    summary_message_ts: Mapped[str] = mapped_column(server_default="", default="", init=False)
    # Slack ts of the "pull request changed" note with its Continue button; empty when not paused.
    paused_message_ts: Mapped[str] = mapped_column(server_default="", default="", init=False)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    closed_at: Mapped[datetime | None] = mapped_column(default=None, init=False)
    pull_request: Mapped[PullRequest] = relationship(init=False)

    @property
    def closed(self) -> bool:
        return self.closed_at is not None

    async def is_reader(self, slack_user_id: str) -> bool:
        """Whether this Slack member is the person being walked through; only they approve."""
        user = await User.for_identity("slack", slack_user_id) if slack_user_id else None
        return user is not None and user.id == self.user_id

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

    async def save_summary_message(self, message_ts: str) -> None:
        self.summary_message_ts = message_ts
        async with postgres.session() as session:
            await session.execute(
                update(ReviewGuideSession)
                .where(ReviewGuideSession.thread_id == self.thread_id)
                .values(summary_message_ts=message_ts)
            )
            await session.commit()

    async def save_pause(self, message_ts: str) -> None:
        """Record the note that paused the walkthrough; an empty ``message_ts`` resumes it."""
        self.paused_message_ts = message_ts
        async with postgres.session() as session:
            await session.execute(
                update(ReviewGuideSession)
                .where(ReviewGuideSession.thread_id == self.thread_id)
                .values(paused_message_ts=message_ts)
            )
            await session.commit()

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
