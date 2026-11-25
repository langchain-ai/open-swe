"""Dashboard API for per-user and organization-wide Agent Skills."""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import JsonValue

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import ADMIN_DEP, SESSION_DEP, DashboardReadDep
from openswe.skill_store.store import (
    DEFAULT_SKILLS_PAGE_SIZE,
    MAX_SKILLS_PAGE_SIZE,
    HubSkills,
    SkillCreate,
    SkillUpdate,
    create_skill,
    delete_skill,
    list_skills,
    update_skill,
)

router = APIRouter()


@router.get("/skills")
async def api_list_skills(
    reader: DashboardReadDep,
    limit: int = Query(DEFAULT_SKILLS_PAGE_SIZE, ge=1, le=MAX_SKILLS_PAGE_SIZE),
    offset: int = Query(0, ge=0),
) -> dict[str, JsonValue]:
    await reader.close()
    return await list_skills(
        reader.principal.subject,
        limit=limit,
        offset=offset,
        user=reader.user,
    )


@router.post("/skills")
@audit_endpoint
async def api_create_skill(
    body: SkillCreate,
    reader: DashboardReadDep,
) -> dict[str, JsonValue]:
    await reader.close()
    return await create_skill(reader.principal.subject, body, user=reader.user)


@router.put("/skills/{name}")
@audit_endpoint
async def api_update_skill(
    name: str,
    body: SkillUpdate,
    reader: DashboardReadDep,
) -> dict[str, JsonValue]:
    await reader.close()
    return await update_skill(reader.principal.subject, name, body, user=reader.user)


@router.delete("/skills/{name}")
@audit_endpoint
async def api_delete_skill(
    name: str,
    reader: DashboardReadDep,
) -> Response:
    await reader.close()
    await delete_skill(reader.principal.subject, name, user=reader.user)
    return Response(status_code=204)


@router.get("/organization-skills")
async def api_list_organization_skills(
    limit: int = Query(DEFAULT_SKILLS_PAGE_SIZE, ge=1, le=MAX_SKILLS_PAGE_SIZE),
    cursor: str | None = Query(None, max_length=256),
    _session: dict[str, JsonValue] = SESSION_DEP,
) -> dict[str, JsonValue]:
    if cursor is not None:
        try:
            cursor = SkillCreate(name=cursor, description="valid").name
        except ValueError as exc:
            raise HTTPException(400, "Invalid cursor") from exc
    async with HubSkills.open() as skills:
        found = [skill for skill in await skills.listed() if str(skill["name"]) > (cursor or "")]
    page = found[:limit]
    return {
        "items": list[JsonValue](page),
        "next_cursor": page[-1]["name"] if len(found) > limit else None,
    }


@router.post("/organization-skills")
@audit_endpoint
async def api_create_organization_skill(
    body: SkillCreate,
    _admin: dict[str, JsonValue] = ADMIN_DEP,
) -> dict[str, JsonValue]:
    async with HubSkills.open() as skills:
        return await skills.save(body, create=True)


@router.put("/organization-skills/{name}")
@audit_endpoint
async def api_update_organization_skill(
    name: str,
    body: SkillUpdate,
    _admin: dict[str, JsonValue] = ADMIN_DEP,
) -> dict[str, JsonValue]:
    async with HubSkills.open() as skills:
        return await skills.save(SkillCreate(name=name, **body.model_dump()), create=False)


@router.delete("/organization-skills/{name}")
@audit_endpoint
async def api_delete_organization_skill(
    name: str,
    _admin: dict[str, JsonValue] = ADMIN_DEP,
) -> Response:
    async with HubSkills.open() as skills:
        await skills.delete(name)
    return Response(status_code=204)
