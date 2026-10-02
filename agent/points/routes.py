"""Dashboard API for the points leaderboard."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from agent.dashboard.deps import SESSION_DEP, session_is_admin
from agent.database import postgres
from agent.points.leaderboard import Leaderboard, LeaderboardPeriod, standings
from agent.users import User

router = APIRouter(tags=["points"])


@router.get("/points/leaderboard")
async def api_points_leaderboard(
    period: LeaderboardPeriod = "7d",
    limit: int = Query(default=25, ge=1, le=100),
    session: dict[str, Any] = SESSION_DEP,
) -> Leaderboard:
    if not postgres.configured():
        raise HTTPException(503, "The points leaderboard is unavailable on this deployment.")
    viewer = await User.for_login("github", str(session["sub"]))
    return await standings(
        period,
        limit=limit,
        current_user_id=viewer.id if viewer is not None else None,
        admin=session_is_admin(session),
    )
