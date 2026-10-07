"""Code reviews people complete on GitHub, assigned or not, for the usage leaderboard.

A review counts once per person per pull request: the first one they submit, with
any verdict, on someone else's pull request, while they have an Open SWE account.
"""

import logging
from datetime import datetime
from uuid import UUID, uuid7

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.users import User

logger = logging.getLogger(__name__)


class _GitHubUser(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    login: str
    type: str = "User"


class _Review(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user: _GitHubUser | None = None


class _PullRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: int
    user: _GitHubUser | None = None


class _Owner(BaseModel):
    model_config = ConfigDict(extra="ignore")

    login: str


class _Repository(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    owner: _Owner


class ReviewSubmitted(BaseModel):
    """GitHub's ``pull_request_review`` webhook with action ``submitted``."""

    model_config = ConfigDict(extra="ignore")

    review: _Review
    pull_request: _PullRequest
    repository: _Repository


class CompletedReview(Base):
    __tablename__ = "completed_review"
    __table_args__ = (UniqueConstraint("user_id", "repository_key", "pr_number"),)

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    repository_key: Mapped[str]
    pr_number: Mapped[int]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    reviewed_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    @classmethod
    async def record(cls, payload: dict[str, object]) -> None:
        """Webhook entry point for a submitted pull request review."""
        if not postgres.configured():
            return
        try:
            event = ReviewSubmitted.model_validate(payload)
        except ValidationError:
            logger.warning("GitHub review event has an unexpected shape", exc_info=True)
            return
        reviewer = event.review.user
        author = event.pull_request.user
        if reviewer is None or reviewer.type != "User":
            return
        if author is None or author.id == reviewer.id:
            return
        if await User.for_identity("github", str(author.id)) is None:
            return
        repository = f"{event.repository.owner.login}/{event.repository.name}".lower()
        extra = {
            "github_login": reviewer.login,
            "repository": repository,
            "pr_number": event.pull_request.number,
        }
        user = await User.for_identity("github", str(reviewer.id))
        if user is None:
            logger.info("Not counting a review by someone without an Open SWE account", extra=extra)
            return
        statement = (
            insert(cls)
            .values(
                id=uuid7(),
                user_id=user.id,
                repository_key=repository,
                pr_number=event.pull_request.number,
            )
            .on_conflict_do_nothing()
            .returning(cls.id)
        )
        async with postgres.session() as session:
            if await session.scalar(statement) is not None:
                logger.info("Counted a completed review", extra=extra)
