"""Who has the most points over a period."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import DateTime, Uuid, bindparam, text

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


_STANDINGS_SQL = text(
    """
WITH totals AS (
    SELECT user_id,
        sum(delta) AS points,
        count(*) FILTER (WHERE reason = 'reviewed') AS reviewed,
        count(*) FILTER (WHERE reason = 'pick_expired') AS missed_picks
    FROM point
    WHERE :start IS NULL OR created_at >= :start
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
            ORDER BY points DESC, reviewed DESC,
                lower(COALESCE(NULLIF(display_name, ''), login)), user_id
        ) AS rank
    FROM scored
)
SELECT rank, name, login, avatar_url, points, reviewed, missed_picks, is_current
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
    points: int
    reviewed: int
    missed_picks: int
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
            points=self.points,
            reviewed=self.reviewed,
            missed_picks=self.missed_picks,
            is_current=self.is_current,
        )


async def standings(
    period: LeaderboardPeriod, *, limit: int, current_user_id: UUID | None, admin: bool
) -> Leaderboard:
    """The top ``limit`` people by points in ``period``, plus the viewer's own standing."""
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
