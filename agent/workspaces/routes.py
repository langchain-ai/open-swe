"""Dashboard API for instance-wide sandbox settings and named workspaces."""

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from agent.dashboard.deps import ADMIN_DEP, ADMIN_OR_TOKEN_DEP, SESSION_DEP, session_is_admin
from agent.dashboard.workspace_settings import delete_workspace_settings
from agent.workspaces.refresh import (
    ensure_refresh_cron,
    is_refresh_in_flight,
    start_refresh_run,
)
from agent.workspaces.sandbox_settings import (
    SandboxSettingsUpdate,
    get_sandbox_settings,
    upsert_sandbox_settings,
)
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


@router.get("/sandbox-settings")
async def api_get_sandbox_settings(
    _admin: dict[str, Any] = ADMIN_OR_TOKEN_DEP,
) -> dict[str, Any]:
    return await get_sandbox_settings()


@router.put("/sandbox-settings")
async def api_set_sandbox_settings(
    body: SandboxSettingsUpdate,
    _admin: dict[str, Any] = ADMIN_OR_TOKEN_DEP,
) -> dict[str, Any]:
    return await upsert_sandbox_settings(body, updated_by=_admin.get("sub"))


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


@router.get("/workspaces/options")
async def api_workspace_options(
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    """Pickable workspaces for any signed-in user; refresh logs only for admins."""
    return {
        "workspaces": await list_workspace_options(include_logs=session_is_admin(session)),
        "default_slug": DEFAULT_WORKSPACE_SLUG,
    }


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
    try:
        record = await WORKSPACES.apply_update(_normalized_slug(slug), body)
    except ValueError as e:
        raise _save_conflict(e) from e
    if record.setup_script:
        await ensure_refresh_cron(record.slug)
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
