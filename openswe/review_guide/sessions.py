"""Review guide sessions and the changed lines each person has approved.

A session is one code channel walking one person through one pull request,
along with where the walkthrough of its current head stands. What they approved is kept per
person and pull request as line ranges at one head, carried to each new head
with the plan, so a rebase or force-push never asks them to reread it.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Literal, Self
from uuid import UUID, uuid7

from sqlalchemy import ForeignKey, Text, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.pull_requests import PullRequest
from openswe.github.repositories import Repository
from openswe.review_guide.walk import Reader, Walk
from openswe.users.models import User
from openswe.utils.json_types import JsonObject
from openswe.walkthrough.diff import Carry
from openswe.walkthrough.lines import LineSet
from openswe.walkthrough.record import Walkthrough

# A reviewer ends by approving; the author, who cannot approve their own PR, ends by readying it.
GuideMode = Literal["reviewer", "author"]


class ReviewGuideSeen(Base):
    __tablename__ = "review_guide_seen"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey("pull_request.id", ondelete="CASCADE"))
    # The head and merge base ``lines`` are numbered at; another head carries them there first.
    head_sha: Mapped[str]
    merge_base_sha: Mapped[str]
    lines_json: Mapped[JsonObject] = mapped_column("lines", JSONB)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    @property
    def lines(self) -> LineSet:
        return LineSet.load(self.lines_json)


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

    async def reader(self) -> Reader | None:
        """The reader's walk over the stored plan, or ``None`` until both are at the same head."""
        walk = self.walk
        stored = await Walkthrough.get(self.pull_request_id)
        if walk is None or stored is None or stored.head_sha != walk.head_sha:
            return None
        return Reader.of(walk, stored.plan, await self.seen_at(walk.head_sha))

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

    async def seen(self) -> ReviewGuideSeen | None:
        """What this person has approved on this pull request, at the head it was last carried to."""
        async with postgres.session() as session:
            return await session.scalar(
                select(ReviewGuideSeen).where(
                    ReviewGuideSeen.user_id == self.user_id,
                    ReviewGuideSeen.pull_request_id == self.pull_request_id,
                )
            )

    async def seen_at(self, head_sha: str) -> LineSet:
        """What this person has approved, if it is numbered at ``head_sha``; nothing otherwise."""
        seen = await self.seen()
        return seen.lines if seen is not None and seen.head_sha == head_sha else LineSet()

    async def _update_seen(
        self,
        head_sha: str,
        merge_base_sha: str,
        change: Callable[[ReviewGuideSeen | None], LineSet | None],
        *,
        by: UUID | None,
    ) -> None:
        async with postgres.session() as session:
            await session.execute(
                insert(ReviewGuideSeen)
                .values(
                    id=uuid7(),
                    user_id=self.user_id,
                    pull_request_id=self.pull_request_id,
                    head_sha=head_sha,
                    merge_base_sha=merge_base_sha,
                    lines_json={},
                    created_by=by,
                    updated_by=by,
                )
                .on_conflict_do_nothing(
                    index_elements=[ReviewGuideSeen.user_id, ReviewGuideSeen.pull_request_id]
                )
            )
            row = await session.scalar(
                select(ReviewGuideSeen)
                .where(
                    ReviewGuideSeen.user_id == self.user_id,
                    ReviewGuideSeen.pull_request_id == self.pull_request_id,
                )
                .with_for_update()
            )
            lines = change(row)
            if row is not None and lines is not None:
                row.head_sha = head_sha
                row.merge_base_sha = merge_base_sha
                row.lines_json = lines.dump()
                row.updated_by = by
            await session.commit()

    async def mark_seen(self, head_sha: str, merge_base_sha: str, lines: LineSet) -> None:
        """Add ``lines``, numbered at ``head_sha`` and ``merge_base_sha``, to what this person approved."""
        if not lines:
            return

        def add(row: ReviewGuideSeen | None) -> LineSet:
            kept = row.lines if row is not None and row.head_sha == head_sha else LineSet()
            return kept | lines

        await self._update_seen(head_sha, merge_base_sha, add, by=self.user_id)

    async def carry_seen(
        self, head_sha: str, merge_base_sha: str, carry: Carry | None, from_sha: str
    ) -> None:
        """Carry what this person approved at ``from_sha`` to ``head_sha``, if it is still there.

        With no ``carry``, because ``from_sha`` can no longer be read, nothing carries over.
        """

        def moved(row: ReviewGuideSeen | None) -> LineSet | None:
            if row is None or row.head_sha != from_sha:
                return None
            return carry.lines(row.lines) if carry is not None else LineSet()

        await self._update_seen(head_sha, merge_base_sha, moved, by=None)
