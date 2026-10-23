"""Dashboard API for usage, PR and pipeline analytics."""

import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from openswe.analytics.queries import (
    InvalidUsageCursor,
    SortDirection,
    UsageSort,
    usage_leaderboard,
)
from openswe.config import ENV
from openswe.dashboard.deps import ADMIN_DEP, SESSION_DEP, session_is_admin

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analytics"])


class TelemetryConfig(BaseModel):
    environment: str


@router.get("/analytics/config")
async def api_telemetry_config() -> TelemetryConfig:
    return TelemetryConfig(environment=ENV.DD_ENV.get())


class PageView(BaseModel):
    page_name: Literal[
        "agents",
        "review",
        "usage",
        "settings",
        "workspaces",
        "integrations",
        "admin",
        "assistant",
        "incidents",
        "cloud-agents",
        "feature-flags",
        "other",
    ]


@router.post("/analytics/page", status_code=204)
async def api_page_view(
    body: PageView,
    session: dict[str, Any] = SESSION_DEP,
) -> None:
    from openswe.analytics.posthog import record_usage

    await record_usage(
        login=session["sub"],
        email=session.get("email"),
        event_type="page",
        name=body.page_name,
        properties={"page_name": body.page_name, "surface": "dashboard"},
    )


@router.get("/agent-usage-leaderboard")
async def api_agent_usage_leaderboard(
    period: str | None = "30d",
    limit: int = Query(default=10, ge=1, le=100),
    cursor: str | None = None,
    sort: UsageSort = "rank",
    direction: SortDirection = "asc",
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    from asyncpg import PostgresError
    from sqlalchemy.exc import SQLAlchemyError

    from openswe.database import configured

    try:
        if not configured():
            raise HTTPException(503, "Usage analytics is unavailable on this deployment.")
        return await usage_leaderboard(
            period=period,
            limit=limit,
            cursor=cursor,
            sort=sort,
            direction=direction,
            current_login=session["sub"],
            current_email=session.get("email"),
            admin=session_is_admin(session),
        )
    except InvalidUsageCursor as exc:
        logger.info(
            "Usage analytics request rejected",
            extra={"analytics_error_type": type(exc).__name__, "analytics_error": str(exc)},
        )
        raise HTTPException(400, str(exc)) from exc
    except (SQLAlchemyError, PostgresError, OSError, RuntimeError, ValueError) as exc:
        logger.warning(
            "Usage analytics report unavailable",
            extra={"analytics_error_type": type(exc).__name__, "analytics_error": str(exc)},
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

    from openswe.analytics.queries import pr_merge_rate_by_model
    from openswe.database import configured

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
    from openswe.database.analytics import readiness

    return await readiness()


@router.get("/analytics/outbox-status")
async def api_analytics_outbox_status(
    _admin: dict[str, Any] = ADMIN_DEP,
) -> dict[str, Any]:
    from openswe.analytics.outbox import outbox_status

    return await outbox_status()
