"""Named agent environments, and the short list any signed-in user may pick from."""

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from ...settings.environments import (
    DEFAULT_ENVIRONMENT_SLUG,
    EnvironmentCreate,
    EnvironmentUpdate,
    create_environment,
    delete_environment,
    get_environment,
    list_environment_options,
    list_environments,
    slugify,
    update_environment,
)
from ..authz import ADMIN, SESSION

router = APIRouter()


def _normalized_slug(raw: str) -> str:
    try:
        return slugify(raw)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/environments")
async def api_list_environments(
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, Any]:
    return {
        "environments": await list_environments(),
        "default_slug": DEFAULT_ENVIRONMENT_SLUG,
    }


@router.post("/environments")
async def api_create_environment(
    body: EnvironmentCreate,
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, Any]:
    try:
        return await create_environment(body, _admin["sub"])
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/environments/options")
async def api_environment_options(
    _session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    """Pickable environments for any signed-in user: names only, no prompts."""
    return {
        "environments": await list_environment_options(),
        "default_slug": DEFAULT_ENVIRONMENT_SLUG,
    }


@router.get("/environments/{slug}")
async def api_get_environment(
    slug: str,
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, Any]:
    record = await get_environment(_normalized_slug(slug))
    if not record:
        raise HTTPException(404, "environment not found")
    return record


@router.put("/environments/{slug}")
async def api_update_environment(
    slug: str,
    body: EnvironmentUpdate,
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, Any]:
    try:
        return await update_environment(_normalized_slug(slug), body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/environments/{slug}")
async def api_delete_environment(
    slug: str,
    _admin: dict[str, Any] = ADMIN,
) -> Response:
    if not await delete_environment(_normalized_slug(slug)):
        raise HTTPException(404, "environment not found")
    return Response(status_code=204)
