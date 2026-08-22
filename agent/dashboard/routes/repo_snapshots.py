"""Per-repo sandbox images: the Dockerfile, its build, and their status."""

from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import Response

from ...settings.repo_snapshots import (
    RepoSnapshotConfigError,
    RepoSnapshotCreate,
    RepoSnapshotUpdate,
    create_repo_snapshot,
    delete_repo_snapshot,
    generate_dockerfile_template,
    get_repo_snapshot,
    is_repo_snapshot_build_stale,
    list_repo_snapshots,
    mark_repo_snapshot_building,
    run_snapshot_build,
    update_repo_snapshot,
)
from ...settings.review_styles import normalize_repo_full_name
from ..authz import ADMIN

router = APIRouter()


@router.get("/repo-snapshots")
async def api_list_repo_snapshots(
    _admin: dict[str, Any] = ADMIN,
) -> list[dict[str, Any]]:
    return await list_repo_snapshots()


@router.get("/repo-snapshots/template")
async def api_repo_snapshot_template(
    full_name: str,
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, str]:
    try:
        return {"dockerfile": generate_dockerfile_template(normalize_repo_full_name(full_name))}
    except RepoSnapshotConfigError as e:
        raise HTTPException(500, str(e)) from e


@router.post("/repo-snapshots")
async def api_create_repo_snapshot(
    body: RepoSnapshotCreate,
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, Any]:
    try:
        return await create_repo_snapshot(body.full_name, _admin["sub"])
    except RepoSnapshotConfigError as e:
        raise HTTPException(500, str(e)) from e


@router.get("/repo-snapshots/{full_name:path}")
async def api_get_repo_snapshot(
    full_name: str,
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, Any]:
    record = await get_repo_snapshot(normalize_repo_full_name(full_name))
    if not record:
        raise HTTPException(404, "repo snapshot not found")
    return record


@router.put("/repo-snapshots/{full_name:path}")
async def api_update_repo_snapshot(
    full_name: str,
    body: RepoSnapshotUpdate,
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, Any]:
    return await update_repo_snapshot(normalize_repo_full_name(full_name), body)


@router.post("/repo-snapshots/{full_name:path}/build")
async def api_build_repo_snapshot(
    full_name: str,
    background_tasks: BackgroundTasks,
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, Any]:
    full_name = normalize_repo_full_name(full_name)
    record = await get_repo_snapshot(full_name)
    if not record:
        raise HTTPException(404, "repo snapshot not found")
    if not (record.get("dockerfile") or "").strip():
        raise HTTPException(400, "dockerfile is empty")
    if record.get("status") == "building" and not is_repo_snapshot_build_stale(record):
        raise HTTPException(409, "a build is already in progress")
    record = await mark_repo_snapshot_building(full_name)
    background_tasks.add_task(run_snapshot_build, full_name)
    return record


@router.delete("/repo-snapshots/{full_name:path}")
async def api_delete_repo_snapshot(
    full_name: str,
    _admin: dict[str, Any] = ADMIN,
) -> Response:
    full_name = normalize_repo_full_name(full_name)
    record = await get_repo_snapshot(full_name)
    if not record:
        raise HTTPException(404, "repo snapshot not found")
    await delete_repo_snapshot(full_name)
    return Response(status_code=204)
