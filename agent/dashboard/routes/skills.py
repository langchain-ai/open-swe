"""Skills: the caller's own, and the ones an admin publishes to the whole org."""

from collections.abc import Awaitable
from typing import Any, TypeVar

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from ..authz import ADMIN, SESSION
from ..skills import (
    DEFAULT_SKILLS_PAGE_SIZE,
    MAX_SKILLS_PAGE_SIZE,
    SkillCreate,
    SkillError,
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

T = TypeVar("T")


async def _mapped(call: Awaitable[T]) -> T:
    """Answer a rejected skill request with the status the store asked for."""
    try:
        return await call
    except SkillError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.get("/skills")
async def api_list_skills(
    limit: int = Query(DEFAULT_SKILLS_PAGE_SIZE, ge=1, le=MAX_SKILLS_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    return await _mapped(list_skills(session["sub"], limit=limit, offset=offset))


@router.post("/skills")
async def api_create_skill(
    body: SkillCreate,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    return await _mapped(create_skill(session["sub"], body))


@router.put("/skills/{name}")
async def api_update_skill(
    name: str,
    body: SkillUpdate,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    return await _mapped(update_skill(session["sub"], name, body))


@router.delete("/skills/{name}")
async def api_delete_skill(
    name: str,
    session: dict[str, Any] = SESSION,
) -> Response:
    await _mapped(delete_skill(session["sub"], name))
    return Response(status_code=204)


@router.get("/organization-skills")
async def api_list_organization_skills(
    limit: int = Query(DEFAULT_SKILLS_PAGE_SIZE, ge=1, le=MAX_SKILLS_PAGE_SIZE),
    cursor: str | None = Query(None, max_length=256),
    _session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    return await _mapped(list_organization_skills(limit=limit, cursor=cursor))


@router.post("/organization-skills")
async def api_create_organization_skill(
    body: SkillCreate,
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, Any]:
    return await _mapped(create_organization_skill(body))


@router.put("/organization-skills/{name}")
async def api_update_organization_skill(
    name: str,
    body: SkillUpdate,
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, Any]:
    return await _mapped(update_organization_skill(name, body))


@router.delete("/organization-skills/{name}")
async def api_delete_organization_skill(
    name: str,
    _admin: dict[str, Any] = ADMIN,
) -> Response:
    await _mapped(delete_organization_skill(name))
    return Response(status_code=204)
