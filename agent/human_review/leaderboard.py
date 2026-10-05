"""Who has completed the most code reviews on GitHub, assigned or not.

A review counts once per person per pull request: the first one they submit, with
any verdict, on someone else's pull request, while they have an Open SWE account.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid7

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, Uuid, bindparam, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.users import User

logger = logging.getLogger(__name__)

LeaderboardPeriod = Literal["24h", "7d", "30d", "all"]
_PERIODS: dict[LeaderboardPeriod, timedelta | None] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}


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
        if author is not None and author.id == reviewer.id:
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


class LeaderboardUser(BaseModel):
    name: str
    github_login: str | None
    avatar_url: str | None


class LeaderboardRow(BaseModel):
    rank: int
    user: LeaderboardUser
    reviews: int
    is_current: bool


class Leaderboard(BaseModel):
    period: LeaderboardPeriod
    rows: list[LeaderboardRow]
    current_user: LeaderboardRow | None


_STANDINGS_SQL = text(
    """
WITH totals AS (
    SELECT user_id, count(*) AS reviews
    FROM completed_review
    WHERE :start IS NULL OR reviewed_at >= :start
    GROUP BY user_id
), scored AS (
    SELECT t.*, u.display_name, u.avatar_url,
        COALESCE((
            SELECT ui.login FROM user_identity ui
            WHERE ui.user_id = u.id AND ui.provider = 'github'
            ORDER BY ui.last_seen_at DESC LIMIT 1
        ), '') AS login
    FROM totals t
    JOIN users u ON u.id = t.user_id
), ranked AS (
    SELECT *,
        COALESCE(NULLIF(display_name, ''), NULLIF(login, ''), 'Open SWE user') AS name,
        COALESCE(user_id = :current_user_id, false) AS is_current,
        row_number() OVER (
            ORDER BY reviews DESC, lower(COALESCE(NULLIF(display_name, ''), login)), user_id
        ) AS rank
    FROM scored
)
SELECT rank, name, login, avatar_url, reviews, is_current
FROM ranked
WHERE rank <= :limit OR is_current
ORDER BY rank
"""
).bindparams(
    bindparam("start", type_=DateTime(timezone=True)),
    bindparam("current_user_id", type_=Uuid()),
)


class _Standing(BaseModel):
    rank: int
    name: str
    login: str
    avatar_url: str
    reviews: int
    is_current: bool

    def row(self, *, admin: bool) -> LeaderboardRow:
        github_avatar = f"https://github.com/{self.login}.png?size=80" if self.login else None
        return LeaderboardRow(
            rank=self.rank,
            user=LeaderboardUser(
                name=self.name,
                github_login=(self.login or None) if admin or self.is_current else None,
                avatar_url=self.avatar_url or github_avatar,
            ),
            reviews=self.reviews,
            is_current=self.is_current,
        )


async def standings(
    period: LeaderboardPeriod, *, limit: int, current_user_id: UUID | None, admin: bool
) -> Leaderboard:
    """The top ``limit`` reviewers in ``period``, plus the viewer's own standing."""
    window = _PERIODS[period]
    async with postgres.session() as session:
        result = await session.execute(
            _STANDINGS_SQL,
            {
                "start": datetime.now(UTC) - window if window is not None else None,
                "current_user_id": current_user_id,
                "limit": limit,
            },
        )
        found = [_Standing.model_validate(dict(mapping)) for mapping in result.mappings()]
    rows = [standing.row(admin=admin) for standing in found if standing.rank <= limit]
    mine = next((standing for standing in found if standing.is_current), None)
    return Leaderboard(
        period=period, rows=rows, current_user=mine.row(admin=admin) if mine else None
    )
