"""Dashboard API for recurring agent schedules."""

from typing import Any

from fastapi import APIRouter
from fastapi.responses import Response

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP, session_is_admin
from openswe.schedules.store import (
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
    session: dict[str, Any] = SESSION_DEP,
) -> list[dict[str, Any]]:
    return await list_agent_schedules()


@router.post("/schedules")
@audit_endpoint
async def api_create_schedule(
    body: ScheduleCreateBody,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    return await create_agent_schedule(
        session["sub"],
        body,
        email=session.get("email"),
        allow_admin_thread=session_is_admin(session),
    )


@router.patch("/schedules/{schedule_id}")
@audit_endpoint
async def api_update_schedule(
    schedule_id: str,
    body: ScheduleUpdateBody,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    return await update_agent_schedule(
        schedule_id,
        session["sub"],
        body,
        email=session.get("email"),
        allow_admin_thread=session_is_admin(session),
    )


@router.post("/schedules/{schedule_id}/trigger")
@audit_endpoint
async def api_trigger_schedule(
    schedule_id: str,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    return await trigger_agent_schedule(schedule_id, login=session["sub"])


@router.delete("/schedules/{schedule_id}")
@audit_endpoint
async def api_delete_schedule(
    schedule_id: str,
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    await delete_agent_schedule(schedule_id, login=session["sub"])
    return Response(status_code=204)
