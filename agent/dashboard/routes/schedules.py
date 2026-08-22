"""Recurring agent runs, and the usage leaderboard they show up in."""

from typing import Any

from fastapi import APIRouter
from fastapi.responses import Response

from ..authz import SESSION, session_is_admin
from ..schedule_models import ScheduleCreateBody, ScheduleUpdateBody
from ..schedules import (
    create_agent_schedule,
    delete_agent_schedule,
    list_agent_schedules,
    trigger_agent_schedule,
    update_agent_schedule,
)
from ..usage_reports import list_agent_usage_leaderboard

router = APIRouter()


@router.get("/agent-usage-leaderboard")
async def api_agent_usage_leaderboard(
    period: str | None = "30d",
    limit: int = 10,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    return await list_agent_usage_leaderboard(
        period=period,
        limit=limit,
        current_login=session["sub"],
        current_email=session.get("email"),
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
    return await create_agent_schedule(
        session["sub"],
        body,
        email=session.get("email"),
        allow_admin_thread=session_is_admin(session),
    )


@router.patch("/schedules/{schedule_id}")
async def api_update_schedule(
    schedule_id: str,
    body: ScheduleUpdateBody,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    return await update_agent_schedule(
        schedule_id,
        session["sub"],
        body,
        email=session.get("email"),
        allow_admin_thread=session_is_admin(session),
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
