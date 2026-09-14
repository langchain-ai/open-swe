"""Dashboard API for recurring agent schedules."""

from typing import Any

from fastapi import APIRouter
from fastapi.responses import Response

from agent.dashboard.deps import ADMIN_DEP, SESSION_DEP
from agent.schedules.store import (
    ScheduleCreateBody,
    ScheduleUpdateBody,
    create_agent_schedule,
    delete_agent_schedule,
    list_agent_schedules,
    trigger_agent_schedule,
    update_agent_schedule,
)

router = APIRouter()


@router.get("/schedules")
async def api_list_schedules(
    _session: dict[str, Any] = SESSION_DEP,
) -> list[dict[str, Any]]:
    return await list_agent_schedules()


@router.post("/schedules")
async def api_create_schedule(
    body: ScheduleCreateBody,
    admin: dict[str, Any] = ADMIN_DEP,
) -> dict[str, Any]:
    return await create_agent_schedule(
        admin["sub"], body, email=admin.get("email"), allow_admin_thread=True
    )


@router.patch("/schedules/{schedule_id}")
async def api_update_schedule(
    schedule_id: str,
    body: ScheduleUpdateBody,
    admin: dict[str, Any] = ADMIN_DEP,
) -> dict[str, Any]:
    return await update_agent_schedule(
        schedule_id,
        admin["sub"],
        body,
        email=admin.get("email"),
        allow_admin_thread=True,
    )


@router.post("/schedules/{schedule_id}/trigger")
async def api_trigger_schedule(
    schedule_id: str,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> dict[str, Any]:
    return await trigger_agent_schedule(schedule_id)


@router.delete("/schedules/{schedule_id}")
async def api_delete_schedule(
    schedule_id: str,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> Response:
    await delete_agent_schedule(schedule_id)
    return Response(status_code=204)
