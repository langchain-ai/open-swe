"""Admin-only endpoints: sandbox settings, user mappings, eval status."""

from typing import Any

from fastapi import APIRouter

from ..authz import ADMIN, ADMIN_OR_CI_TOKEN
from ..eval_jobs import get_reviewer_eval_status
from ..sandbox_settings import (
    SandboxSettingsUpdate,
    get_sandbox_settings,
    upsert_sandbox_settings,
)
from ..thread_api import admin_cancel_dashboard_thread
from ..user_mappings import delete_mapping, list_mappings

router = APIRouter()


@router.get("/sandbox-settings")
async def api_get_sandbox_settings(
    _admin: dict[str, Any] = ADMIN_OR_CI_TOKEN,
) -> dict[str, Any]:
    return await get_sandbox_settings()


@router.put("/sandbox-settings")
async def api_set_sandbox_settings(
    body: SandboxSettingsUpdate,
    _admin: dict[str, Any] = ADMIN_OR_CI_TOKEN,
) -> dict[str, Any]:
    return await upsert_sandbox_settings(body, updated_by=_admin.get("sub"))


@router.get("/admin/user-mappings")
async def admin_list_user_mappings(
    page: int = 1,
    page_size: int = 20,
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, Any]:
    page = max(page, 1)
    page_size = max(1, min(page_size, 100))
    records = await list_mappings()
    total = len(records)
    start = (page - 1) * page_size
    items = records[start : start + page_size]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.delete("/admin/user-mappings/{github_login}")
async def admin_delete_user_mapping(
    github_login: str,
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, bool]:
    deleted = await delete_mapping(github_login)
    return {"deleted": deleted}


@router.get("/admin/evals/reviewer")
async def admin_get_reviewer_eval(
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, Any]:
    """Read-only status for the reviewer eval (triggered from the GitHub Action)."""
    return await get_reviewer_eval_status()


@router.post("/admin/threads/{thread_id}/cancel")
async def admin_cancel_thread(
    thread_id: str,
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, Any]:
    return await admin_cancel_dashboard_thread(thread_id)
