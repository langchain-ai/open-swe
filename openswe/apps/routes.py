"""Dashboard API for a person's saved sandbox apps."""

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from openswe.apps.models import AppLaunchError, AppSandboxGoneError, SandboxApp
from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.oauth import session_user_id
from openswe.users import User

logger = logging.getLogger(__name__)

router = APIRouter()


class AppView(BaseModel):
    id: UUID
    name: str
    description: str
    thread_id: str
    port: int
    start_command: str
    workdir: str
    url: str
    created_at: datetime | None
    updated_at: datetime | None


class AppList(BaseModel):
    items: list[AppView]


class AppLaunch(BaseModel):
    url: str
    started: bool


async def _user_id(session: dict[str, Any]) -> UUID:
    user = await User.for_session(session_user_id(session), session["sub"])
    if user is None:
        raise HTTPException(409, "No Open SWE user record for this login yet")
    return user.id


async def _owned_app(session: dict[str, Any], app_id: UUID) -> SandboxApp:
    app = await SandboxApp.owned(await _user_id(session), app_id)
    if app is None:
        raise HTTPException(404, "app not found")
    return app


@router.get("/apps")
async def api_list_apps(session: dict[str, Any] = SESSION_DEP) -> AppList:
    apps = await SandboxApp.for_user(await _user_id(session))
    return AppList(items=[AppView.model_validate(app, from_attributes=True) for app in apps])


@router.post("/apps/{app_id}/launch")
@audit_endpoint
async def api_launch_app(app_id: UUID, session: dict[str, Any] = SESSION_DEP) -> AppLaunch:
    app = await _owned_app(session, app_id)
    try:
        started = await app.launch()
    except AppSandboxGoneError as exc:
        raise HTTPException(410, str(exc)) from exc
    except AppLaunchError as exc:
        logger.warning("Sandbox app failed to start", extra={"app_id": str(app.id)})
        detail = f"{exc}\n\n{exc.log.strip()}" if exc.log.strip() else str(exc)
        raise HTTPException(502, detail) from exc
    return AppLaunch(url=app.url, started=started)


@router.delete("/apps/{app_id}")
@audit_endpoint
async def api_delete_app(app_id: UUID, session: dict[str, Any] = SESSION_DEP) -> Response:
    app = await _owned_app(session, app_id)
    await app.delete()
    return Response(status_code=204)
