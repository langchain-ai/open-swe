"""Dashboard API for named workspaces."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from openswe.audit_logs.context import bind_workspace
from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import ADMIN_DEP, DashboardReadDep, session_is_admin
from openswe.dashboard.profiles import PROFILES
from openswe.dashboard.workspace_settings import (
    WorkspaceSettings,
    delete_workspace_settings,
    fallback_settings,
)
from openswe.users.repository_defaults import RepositoryDefaults
from openswe.workspaces.configuration import configure_workspace, require_kitchen_eligible
from openswe.workspaces.refresh import (
    RefreshStartError,
    ensure_refresh_cron,
    start_refresh,
    start_refresh_run,
)
from openswe.workspaces.store import (
    DEFAULT_WORKSPACE_SLUG,
    WORKSPACES,
    DefaultWorkspaceDeletionError,
    RepositorySettings,
    Workspace,
    WorkspaceConflictError,
    WorkspaceCreate,
    WorkspaceUpdate,
    list_workspace_options,
    slugify,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["workspaces"])


def _normalized_slug(raw: str) -> str:
    try:
        return slugify(raw)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


def _save_conflict(error: ValueError) -> HTTPException:
    """409 when another workspace already holds what this one claims, else 400.

    A slug or binding two admins raced for, or a refresh already running, is a
    conflict the caller can retry after reloading; everything else is a request
    the store refuses.
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
@audit_endpoint
async def api_create_workspace(
    body: WorkspaceCreate,
    request: Request,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> Workspace:
    try:
        await require_kitchen_eligible(body.kitchen_channel_ids + body.ask_channel_ids)
        record = await WORKSPACES.create(body, _admin["sub"])
    except ValueError as e:
        raise _save_conflict(e) from e
    await bind_workspace(request, record)
    if record.setup_script and not record.inherit_default_sandbox:
        await ensure_refresh_cron(record.slug)
        run_id = await start_refresh_run(record.slug)
        if run_id is None:
            raise HTTPException(
                502,
                "workspace was created but its initial image build could not start; "
                "retry the build from workspace settings",
            )
        return record.model_copy(update={"refresh_status": "refreshing", "refresh_run_id": run_id})
    return record


@router.get("/workspaces/options")
async def api_workspace_options(
    reader: DashboardReadDep,
) -> dict[str, Any]:
    """Pickable workspaces for any signed-in user; refresh logs only for admins.

    Each option carries ``default_repo`` so the composer can preselect it when
    the workspace is chosen first.
    """
    options = await list_workspace_options(
        include_logs=session_is_admin(reader.claims), session=reader.session
    )
    profile = None
    try:
        async with reader.session.begin_nested():
            profile = await PROFILES.get(reader.principal.subject, session=reader.session)
    except Exception:
        logger.warning("profile lookup failed", exc_info=True)
    defaults = RepositoryDefaults.model_validate(profile or {})
    missing = [option["slug"] for option in options if not defaults.repo_config(option["slug"])]
    settings: dict[str, WorkspaceSettings] = {}
    if missing:
        try:
            async with reader.session.begin_nested():
                settings = await WorkspaceSettings.load_many(missing, session=reader.session)
        except Exception:
            logger.warning("workspace settings lookup failed; using defaults", exc_info=True)
    fallback = fallback_settings()
    for option in options:
        repo = defaults.repo_config(option["slug"])
        repo = repo or settings.get(option["slug"], fallback).default_repo
        option["default_repo"] = f"{repo['owner']}/{repo['name']}" if repo else None
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
@audit_endpoint
async def api_update_workspace(
    slug: str,
    body: WorkspaceUpdate,
    request: Request,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> Workspace:
    normalized = _normalized_slug(slug)
    previous = await WORKSPACES.get(normalized)
    if previous is None:
        raise HTTPException(400, f"no workspace named {normalized!r}")
    await bind_workspace(request, previous)
    try:
        record = await configure_workspace(previous, body)
    except ValueError as e:
        raise _save_conflict(e) from e
    except RefreshStartError as e:
        raise HTTPException(502, str(e)) from e
    await bind_workspace(request, record)
    return record


class RepositoryConfiguration(BaseModel):
    """Settings to change on one repository. Anything left out keeps its value."""

    may_start_threads: bool | None = None


@router.get("/workspaces/{slug}/repositories")
async def api_list_workspace_repositories(
    slug: str,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> list[RepositorySettings]:
    normalized = _normalized_slug(slug)
    if not await WORKSPACES.slug_exists(normalized):
        raise HTTPException(404, "workspace not found")
    return await WORKSPACES.repository_settings(normalized)


@router.put("/workspaces/{slug}/repositories/{owner}/{name}")
@audit_endpoint
async def api_configure_workspace_repository(
    slug: str,
    owner: str,
    name: str,
    body: RepositoryConfiguration,
    request: Request,
    admin: dict[str, Any] = ADMIN_DEP,
) -> RepositorySettings:
    """Change how one of a workspace's repositories is configured there."""
    normalized = _normalized_slug(slug)
    await bind_workspace(request, normalized)
    try:
        settings = await WORKSPACES.configure_repository(
            normalized, f"{owner}/{name}", may_start_threads=body.may_start_threads
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    logger.info(
        "Configured a workspace repository",
        extra={
            "workspace": normalized,
            "repository": settings.repo,
            "may_start_threads": settings.may_start_threads,
            "changed_by": str(admin.get("sub") or ""),
        },
    )
    return settings


@router.post("/workspaces/{slug}/refresh")
@audit_endpoint
async def api_refresh_workspace(
    slug: str,
    request: Request,
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
    await bind_workspace(request, record)
    try:
        run_id = await start_refresh(record)
    except ValueError as e:
        raise _save_conflict(e) from e
    except RefreshStartError as e:
        raise HTTPException(502, str(e)) from e
    return {"started": True, "run_id": run_id}


@router.delete("/workspaces/{slug}")
@audit_endpoint
async def api_delete_workspace(
    slug: str,
    request: Request,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> Response:
    normalized = _normalized_slug(slug)
    await bind_workspace(request, normalized)
    try:
        removed = await WORKSPACES.remove(normalized)
    except DefaultWorkspaceDeletionError as e:
        raise HTTPException(409, str(e)) from e
    if not removed:
        raise HTTPException(404, "workspace not found")
    # A later workspace under the same slug must start from the instance record.
    await delete_workspace_settings(normalized)
    return Response(status_code=204)
