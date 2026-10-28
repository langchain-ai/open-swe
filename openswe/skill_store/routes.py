"""Dashboard API for per-user and organization-wide Agent Skills."""

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import Response

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import ADMIN_DEP, SESSION_DEP
from openswe.dashboard.oauth import DashboardSession
from openswe.skill_store.store import (
    DEFAULT_SKILLS_PAGE_SIZE,
    MAX_SKILLS_PAGE_SIZE,
    SkillCreate,
    SkillUpdate,
    create_organization_skill,
    create_skill,
    delete_organization_skill,
    delete_skill,
    list_organization_skills,
    list_skills,
    update_organization_skill,
    update_skill,
)

router = APIRouter()


@router.get("/skills")
async def api_list_skills(
    limit: int = Query(DEFAULT_SKILLS_PAGE_SIZE, ge=1, le=MAX_SKILLS_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    session: DashboardSession = SESSION_DEP,
) -> dict[str, Any]:
    return await list_skills(session.login, limit=limit, offset=offset)


@router.post("/skills")
@audit_endpoint
async def api_create_skill(
    body: SkillCreate,
    session: DashboardSession = SESSION_DEP,
) -> dict[str, Any]:
    return await create_skill(session.login, body)


@router.put("/skills/{name}")
@audit_endpoint
async def api_update_skill(
    name: str,
    body: SkillUpdate,
    session: DashboardSession = SESSION_DEP,
) -> dict[str, Any]:
    return await update_skill(session.login, name, body)


@router.delete("/skills/{name}")
@audit_endpoint
async def api_delete_skill(
    name: str,
    session: DashboardSession = SESSION_DEP,
) -> Response:
    await delete_skill(session.login, name)
    return Response(status_code=204)


@router.get("/organization-skills")
async def api_list_organization_skills(
    limit: int = Query(DEFAULT_SKILLS_PAGE_SIZE, ge=1, le=MAX_SKILLS_PAGE_SIZE),
    cursor: str | None = Query(None, max_length=256),
    _session: DashboardSession = SESSION_DEP,
) -> dict[str, Any]:
    return await list_organization_skills(limit=limit, cursor=cursor)


@router.post("/organization-skills")
@audit_endpoint
async def api_create_organization_skill(
    body: SkillCreate,
    _admin: DashboardSession = ADMIN_DEP,
) -> dict[str, Any]:
    return await create_organization_skill(body)


@router.put("/organization-skills/{name}")
@audit_endpoint
async def api_update_organization_skill(
    name: str,
    body: SkillUpdate,
    _admin: DashboardSession = ADMIN_DEP,
) -> dict[str, Any]:
    return await update_organization_skill(name, body)


@router.delete("/organization-skills/{name}")
@audit_endpoint
async def api_delete_organization_skill(
    name: str,
    _admin: DashboardSession = ADMIN_DEP,
) -> Response:
    await delete_organization_skill(name)
    return Response(status_code=204)
