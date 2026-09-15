"""Dashboard API for usage, PR and pipeline analytics."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from agent.analytics.queries import usage_leaderboard
from agent.dashboard.deps import ADMIN_DEP, SESSION_DEP, session_is_admin

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analytics"])


@router.get("/agent-usage-leaderboard")
async def api_agent_usage_leaderboard(
    period: str | None = "30d",
    limit: int = Query(default=10, ge=1, le=100),
    cursor: str | None = None,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    from asyncpg import PostgresError
    from sqlalchemy.exc import SQLAlchemyError

    from agent.database.analytics import configured

    try:
        if not configured():
            raise HTTPException(503, "Usage analytics is unavailable on this deployment.")
        return await usage_leaderboard(
            period=period,
            limit=limit,
            cursor=cursor,
            current_login=session["sub"],
            current_email=session.get("email"),
            admin=session_is_admin(session),
        )
    except (SQLAlchemyError, PostgresError, OSError, RuntimeError, ValueError) as exc:
        logger.warning(
            "Usage analytics report unavailable",
            extra={"analytics_error_type": type(exc).__name__},
        )
        raise HTTPException(503, "Usage analytics is unavailable on this deployment.") from exc


@router.get("/analytics/pr-merge-rate-by-model")
async def api_pr_merge_rate_by_model(
    period: str | None = "30d",
    maturity_days: int | None = Query(default=None, ge=1, le=365),
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    from asyncpg import PostgresError
    from sqlalchemy.exc import SQLAlchemyError

    from agent.analytics.queries import pr_merge_rate_by_model
    from agent.database.analytics import configured

    try:
        if not configured():
            raise HTTPException(503, "PR analytics is unavailable on this deployment.")
        return await pr_merge_rate_by_model(
            period=period,
            maturity_days=maturity_days,
            admin=session_is_admin(session),
        )
    except (SQLAlchemyError, PostgresError, OSError, RuntimeError, ValueError) as exc:
        logger.warning(
            "PR analytics report unavailable",
            extra={"analytics_error_type": type(exc).__name__},
        )
        raise HTTPException(503, "PR analytics is unavailable on this deployment.") from exc


@router.get("/analytics/readiness")
async def api_analytics_readiness(
    _admin: dict[str, Any] = ADMIN_DEP,
) -> dict[str, Any]:
    from agent.database.analytics import readiness

    return await readiness()


@router.get("/analytics/outbox-status")
async def api_analytics_outbox_status(
    _admin: dict[str, Any] = ADMIN_DEP,
) -> dict[str, Any]:
    from agent.analytics.outbox import outbox_status

    return await outbox_status()
