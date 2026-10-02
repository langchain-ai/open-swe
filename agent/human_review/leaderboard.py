"""Code review points: an append-only ledger of every point awarded or taken, and why.

A person earns ``REVIEWED_POINTS`` the first time they review a pull request on
GitHub, assigned or not, and loses ``MISSED_PICK_POINTS`` when Open SWE picks
them and they do not accept in time. The leaderboard sums the ledger.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid7

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, bindparam, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from agent.database import postgres
from agent.database.orm import NOW, Base

logger = logging.getLogger(__name__)

PointReason = Literal["reviewed", "pick_expired"]
REVIEWED_POINTS = 1
MISSED_PICK_POINTS = -1
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


class ReviewPoint(Base):
    __tablename__ = "review_point"

    github_id: Mapped[int] = mapped_column(BigInteger)
    github_login: Mapped[str]
    delta: Mapped[int]
    reason: Mapped[PointReason] = mapped_column(Text)
    repository_key: Mapped[str]
    pr_number: Mapped[int]
    request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("human_review_request.id", ondelete="SET NULL"), default=None
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    @classmethod
    async def award(
        cls,
        *,
        github_id: int,
        github_login: str,
        reason: PointReason,
        repository_key: str,
        pr_number: int,
        request_id: UUID | None = None,
    ) -> bool:
        """Write one ledger entry; ``False`` when the same award was already recorded."""
        delta = REVIEWED_POINTS if reason == "reviewed" else MISSED_PICK_POINTS
        statement = (
            insert(cls)
            .values(
                id=uuid7(),
                github_id=github_id,
                github_login=github_login,
                delta=delta,
                reason=reason,
                repository_key=repository_key.lower(),
                pr_number=pr_number,
                request_id=request_id,
            )
            .on_conflict_do_nothing()
            .returning(cls.id)
        )
        async with postgres.session() as session:
            recorded = await session.scalar(statement) is not None
        if recorded:
            logger.info(
                "Recorded review points",
                extra={
                    "github_login": github_login,
                    "point_reason": reason,
                    "delta": delta,
                    "repository": repository_key,
                    "pr_number": pr_number,
                },
            )
        return recorded

    @classmethod
    async def missed_pick_ids(cls, request_id: UUID) -> set[int]:
        """GitHub ids of everyone this request rotated away from for not accepting."""
        async with postgres.session() as session:
            rows = await session.scalars(
                select(cls.github_id).where(
                    cls.request_id == request_id, cls.reason == "pick_expired"
                )
            )
            return set(rows.all())


async def record_code_review(payload: dict[str, object]) -> None:
    """Webhook entry point: a human's first review of someone else's pull request earns points."""
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
    await ReviewPoint.award(
        github_id=reviewer.id,
        github_login=reviewer.login,
        reason="reviewed",
        repository_key=f"{event.repository.owner.login}/{event.repository.name}",
        pr_number=event.pull_request.number,
    )


class LeaderboardUser(BaseModel):
    name: str
    github_login: str | None
    avatar_url: str | None


class LeaderboardRow(BaseModel):
    rank: int
    user: LeaderboardUser
    points: int
    reviewed: int
    missed_picks: int
    is_current: bool


class CodeReviewLeaderboard(BaseModel):
    period: LeaderboardPeriod
    rows: list[LeaderboardRow]
    current_user: LeaderboardRow | None


# Only people with an Open SWE account appear; points earned before they sign up still count.
_STANDINGS_SQL = text(
    """
WITH scored AS (
    SELECT u.id AS user_id,
        max(u.display_name) AS display_name,
        max(ui.login) AS login,
        sum(rp.delta) AS points,
        count(*) FILTER (WHERE rp.reason = 'reviewed') AS reviewed,
        count(*) FILTER (WHERE rp.reason = 'pick_expired') AS missed_picks
    FROM review_point rp
    JOIN user_identity ui ON ui.provider = 'github' AND ui.external_id = rp.github_id::text
    JOIN users u ON u.id = ui.user_id
    WHERE :start IS NULL OR rp.created_at >= :start
    GROUP BY u.id
), ranked AS (
    SELECT *,
        COALESCE(NULLIF(display_name, ''), login) AS name,
        lower(login) = :current_login AS is_current,
        row_number() OVER (
            ORDER BY points DESC, reviewed DESC,
                lower(COALESCE(NULLIF(display_name, ''), login)), user_id
        ) AS rank
    FROM scored
)
SELECT rank, name, login, points, reviewed, missed_picks, is_current
FROM ranked
WHERE rank <= :limit OR is_current
ORDER BY rank
"""
).bindparams(bindparam("start", type_=DateTime(timezone=True)))


class _Standing(BaseModel):
    rank: int
    name: str
    login: str
    points: int
    reviewed: int
    missed_picks: int
    is_current: bool

    def row(self, *, admin: bool) -> LeaderboardRow:
        return LeaderboardRow(
            rank=self.rank,
            user=LeaderboardUser(
                name=self.name,
                github_login=self.login if admin or self.is_current else None,
                avatar_url=f"https://github.com/{self.login}.png?size=80",
            ),
            points=self.points,
            reviewed=self.reviewed,
            missed_picks=self.missed_picks,
            is_current=self.is_current,
        )


async def standings(
    period: LeaderboardPeriod, *, limit: int, current_login: str, admin: bool
) -> CodeReviewLeaderboard:
    """The top ``limit`` reviewers by points in ``period``, plus the viewer's own standing."""
    window = _PERIODS[period]
    async with postgres.session() as session:
        result = await session.execute(
            _STANDINGS_SQL,
            {
                "start": datetime.now(UTC) - window if window is not None else None,
                "current_login": current_login.strip().lower(),
                "limit": limit,
            },
        )
        found = [_Standing.model_validate(dict(mapping)) for mapping in result.mappings()]
    rows = [standing.row(admin=admin) for standing in found if standing.rank <= limit]
    mine = next((standing for standing in found if standing.is_current), None)
    return CodeReviewLeaderboard(
        period=period, rows=rows, current_user=mine.row(admin=admin) if mine else None
    )
