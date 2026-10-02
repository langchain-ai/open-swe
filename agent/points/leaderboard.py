"""Who has the most points over a period."""

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import DateTime, bindparam, text

from agent.database import postgres

LeaderboardPeriod = Literal["24h", "7d", "30d", "all"]
_PERIODS: dict[LeaderboardPeriod, timedelta | None] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}


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


class Leaderboard(BaseModel):
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
        sum(p.delta) AS points,
        count(*) FILTER (WHERE p.reason = 'reviewed') AS reviewed,
        count(*) FILTER (WHERE p.reason = 'pick_expired') AS missed_picks
    FROM point p
    JOIN user_identity ui ON ui.provider = 'github' AND ui.external_id = p.github_id::text
    JOIN users u ON u.id = ui.user_id
    WHERE :start IS NULL OR p.created_at >= :start
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
) -> Leaderboard:
    """The top ``limit`` people by points in ``period``, plus the viewer's own standing."""
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
    return Leaderboard(
        period=period, rows=rows, current_user=mine.row(admin=admin) if mine else None
    )
