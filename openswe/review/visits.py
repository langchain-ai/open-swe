"""The review pages each person opened, most recent first, for the Recent tab."""

from datetime import datetime
from uuid import UUID, uuid7

from pydantic import BaseModel
from sqlalchemy import ForeignKey, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.comments import PrState
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequest, PullRequestEvent
from openswe.users.models import User


class RecentReviewPage(BaseModel):
    owner: str
    repo: str
    number: int
    title: str
    author: str
    state: PrState
    opened_at: datetime


class ReviewPageVisit(Base):
    """One person's latest visit to one pull request's review page; ``updated_at`` is when."""

    __tablename__ = "review_page_visit"

    user_id: Mapped[UUID] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"))
    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey(PullRequest.id, ondelete="CASCADE"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )

    @classmethod
    async def record(cls, user: User, pull: PullRequestClient) -> None:
        """Note that ``user`` opened ``pull``'s review page, filling in a PR row we never saw."""
        pull_request = await PullRequest(
            owner=pull.repo.owner, repo=pull.repo.name, number=pull.number
        ).ensure()
        if not pull_request.title:
            event = PullRequestEvent.parse(
                {
                    "pull_request": await pull.pull(),
                    "repository": {"full_name": pull.repo.full_name},
                }
            )
            fetched = event.to_pull_request() if event is not None else None
            if fetched is not None:
                pull_request = await fetched.save()
        statement = (
            insert(cls)
            .values(
                id=uuid7(),
                user_id=user.id,
                pull_request_id=pull_request.id,
                created_by=user.id,
                updated_by=user.id,
            )
            .on_conflict_do_update(
                index_elements=[cls.user_id, cls.pull_request_id],
                set_={"updated_at": func.clock_timestamp(), "updated_by": user.id},
            )
        )
        async with postgres.session() as session:
            await session.execute(statement)

    @classmethod
    async def recent(
        cls, user: User, *, repositories: frozenset[str], limit: int
    ) -> list[RecentReviewPage]:
        """``user``'s latest review pages among ``repositories`` (lowercase full names)."""
        statement = (
            select(PullRequest, cls.updated_at)
            .join(cls, cls.pull_request_id == PullRequest.id)
            .where(
                cls.user_id == user.id,
                func.lower(PullRequest.owner + "/" + PullRequest.repo).in_(repositories),
            )
            .order_by(cls.updated_at.desc())
            .limit(limit)
        )
        async with postgres.session() as session:
            rows = (await session.execute(statement)).all()
        return [
            RecentReviewPage(
                owner=pull_request.owner,
                repo=pull_request.repo,
                number=pull_request.number,
                title=pull_request.title,
                author=pull_request.author,
                state=pull_request.state,
                opened_at=opened_at,
            )
            for pull_request, opened_at in rows
        ]
