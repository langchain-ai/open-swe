"""Recurring agent runs, and the usage leaderboard they show up in."""

from typing import Any

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import Response

from ...settings.agent_usage import (
    list_agent_usage_leaderboard,
    refresh_reviewer_stats_cache,
    refresh_usage_leaderboard_cache,
)
from ..authz import SESSION
from ..schedule_models import ScheduleCreateBody, ScheduleUpdateBody
from ..schedules import (
    create_agent_schedule,
    delete_agent_schedule,
    list_agent_schedules,
    trigger_agent_schedule,
    update_agent_schedule,
)

router = APIRouter()


@router.get("/agent-usage-leaderboard")
async def api_agent_usage_leaderboard(
    background_tasks: BackgroundTasks,
    period: str | None = "30d",
    limit: int = 10,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    return await list_agent_usage_leaderboard(
        period=period,
        limit=limit,
        current_login=session["sub"],
        current_email=session.get("email"),
        schedule_usage_refresh=lambda cache_period: background_tasks.add_task(
            refresh_usage_leaderboard_cache, cache_period
        ),
        schedule_reviewer_refresh=lambda cache_period: background_tasks.add_task(
            refresh_reviewer_stats_cache, cache_period
        ),
    )


@router.get("/schedules")
async def api_list_schedules(
    session: dict[str, Any] = SESSION,
) -> list[dict[str, Any]]:
    return await list_agent_schedules(session["sub"], email=session.get("email"))


@router.post("/schedules")
async def api_create_schedule(
    body: ScheduleCreateBody,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    return await create_agent_schedule(session["sub"], body, email=session.get("email"))


@router.patch("/schedules/{schedule_id}")
async def api_update_schedule(
    schedule_id: str,
    body: ScheduleUpdateBody,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    return await update_agent_schedule(
        schedule_id, session["sub"], body, email=session.get("email")
    )


@router.post("/schedules/{schedule_id}/trigger")
async def api_trigger_schedule(
    schedule_id: str,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    return await trigger_agent_schedule(schedule_id, session["sub"], email=session.get("email"))


@router.delete("/schedules/{schedule_id}")
async def api_delete_schedule(
    schedule_id: str,
    session: dict[str, Any] = SESSION,
) -> Response:
    await delete_agent_schedule(schedule_id, session["sub"], email=session.get("email"))
    return Response(status_code=204)
