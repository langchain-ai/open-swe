"""Dashboard API for named workspaces."""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from agent.dashboard.deps import ADMIN_DEP, SESSION_DEP, session_is_admin
from agent.dashboard.workspace_settings import delete_workspace_settings, get_workspace_settings
from agent.workspaces.refresh import (
    ensure_refresh_cron,
    is_refresh_in_flight,
    start_refresh_run,
)
from agent.workspaces.routing import workspace_for_repo
from agent.workspaces.store import (
    DEFAULT_WORKSPACE_SLUG,
    WORKSPACES,
    Workspace,
    WorkspaceConflictError,
    WorkspaceCreate,
    WorkspaceUpdate,
    list_workspace_options,
    slugify,
)

router = APIRouter(tags=["workspaces"])


def _normalized_slug(raw: str) -> str:
    try:
        return slugify(raw)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


def _save_conflict(error: ValueError) -> HTTPException:
    """409 when another workspace already holds what this one claims, else 400.

    A slug or binding two admins raced for is a conflict the caller can retry
    after reloading; everything else is a definition the store refuses.
    """
    if isinstance(error, WorkspaceConflictError):
        return HTTPException(409, str(error))
    return HTTPException(400, str(error))


@router.get("/workspaces")
async def api_list_workspaces(
    _admin: dict[str, Any] = ADMIN_DEP,
) -> dict[str, Any]:
    return {
        "workspaces": await WORKSPACES.list_all(),
        "default_slug": DEFAULT_WORKSPACE_SLUG,
    }


@router.post("/workspaces")
async def api_create_workspace(
    body: WorkspaceCreate,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> Workspace:
    try:
        record = await WORKSPACES.create(body, _admin["sub"])
    except ValueError as e:
        raise _save_conflict(e) from e
    if record.setup_script:
        await ensure_refresh_cron(record.slug)
    return record


async def _default_repo_for(slug: str) -> str | None:
    """The repository a run composed in ``slug`` starts from when none is picked.

    Resolved through the settings tiers, then subject to ownership: a default
    the workspace inherited from the instance is withheld when another
    workspace owns that repository, the same rule Slack routing applies.
    """
    repo = (await get_workspace_settings(slug)).default_repo
    if not repo:
        return None
    owner = await workspace_for_repo(repo["owner"], repo["name"])
    if owner is not None and owner != slug:
        return None
    return f"{repo['owner']}/{repo['name']}"


@router.get("/workspaces/options")
async def api_workspace_options(
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    """Pickable workspaces for any signed-in user; refresh logs only for admins.

    Each option carries ``default_repo`` so the composer can preselect it when
    the workspace is chosen first.
    """
    options = await list_workspace_options(include_logs=session_is_admin(session))
    defaults = await asyncio.gather(*(_default_repo_for(option["slug"]) for option in options))
    for option, default_repo in zip(options, defaults, strict=True):
        option["default_repo"] = default_repo
    return {"workspaces": options, "default_slug": DEFAULT_WORKSPACE_SLUG}


@router.get("/workspaces/{slug}")
async def api_get_workspace(
    slug: str,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> Workspace:
    record = await WORKSPACES.get(_normalized_slug(slug))
    if not record:
        raise HTTPException(404, "workspace not found")
    return record


@router.put("/workspaces/{slug}")
async def api_update_workspace(
    slug: str,
    body: WorkspaceUpdate,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> Workspace:
    normalized = _normalized_slug(slug)
    previous = await WORKSPACES.get(normalized)
    repos_changed = (
        previous is not None
        and body.repos is not None
        and {repo.lower() for repo in body.repos} != {repo.lower() for repo in previous.repos}
    )
    if repos_changed and is_refresh_in_flight(previous):
        raise HTTPException(409, "a refresh of this workspace is already running")
    try:
        record = await WORKSPACES.apply_update(normalized, body)
    except ValueError as e:
        raise _save_conflict(e) from e
    if record.setup_script:
        await ensure_refresh_cron(record.slug)
        if repos_changed and await start_refresh_run(record.slug) is None:
            raise HTTPException(502, "workspace was saved but its snapshot rebuild could not start")
    return record


@router.post("/workspaces/{slug}/refresh")
async def api_refresh_workspace(
    slug: str,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> dict[str, Any]:
    """Start a snapshot rebuild from the workspace's scripts.

    Started in the background rather than awaited: a rebuild takes minutes, and
    the outcome lands on the record for the dashboard to poll.
    """
    normalized = _normalized_slug(slug)
    record = await WORKSPACES.get(normalized)
    if not record:
        raise HTTPException(404, "workspace not found")
    if not record.setup_script:
        raise HTTPException(400, "workspace has no setup script to run")
    if is_refresh_in_flight(record):
        raise HTTPException(409, "a refresh of this workspace is already running")
    run_id = await start_refresh_run(normalized)
    if run_id is None:
        raise HTTPException(502, "could not start the refresh job")
    return {"started": True, "run_id": run_id}


@router.delete("/workspaces/{slug}")
async def api_delete_workspace(
    slug: str,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> Response:
    normalized = _normalized_slug(slug)
    if not await WORKSPACES.remove(normalized):
        raise HTTPException(404, "workspace not found")
    # A later workspace under the same slug must start from the instance record.
    await delete_workspace_settings(normalized)
    return Response(status_code=204)
