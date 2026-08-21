"""Per-repo review style: the synthesized prompt and the analysis that writes it."""

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from ...review.analyzer_cron import remove_continual_cron
from ...review.style_jobs import (
    cancel_review_style_analysis,
    start_bootstrap_analysis,
    sync_review_style_run_status,
)
from ...settings.review_styles import (
    ReviewStyleCreate,
    ReviewStylePromptUpdate,
    create_review_style,
    delete_review_style,
    get_review_style,
    list_review_styles,
    set_custom_prompt,
)
from ..authz import REPO_FULL_NAME_ACCESS, SESSION, RepoAccess
from ..repo_access import filter_repo_records_for_user, require_repo_access_for_user

router = APIRouter()


@router.get("/review-styles")
async def api_list_review_styles(
    session: dict[str, Any] = SESSION,
) -> list[dict[str, Any]]:
    records = await filter_repo_records_for_user(session["sub"], await list_review_styles())
    out: list[dict[str, Any]] = []
    for record in records:
        if record.get("status") == "running":
            synced = await sync_review_style_run_status(record["full_name"])
            out.append(synced)
        else:
            out.append(record)
    return out


@router.post("/review-styles")
async def api_create_review_style(
    body: ReviewStyleCreate,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], body.full_name)
    return await create_review_style(body.full_name, session["sub"])


@router.get("/review-styles/{full_name:path}")
async def api_get_review_style(
    access: RepoAccess = REPO_FULL_NAME_ACCESS,
) -> dict[str, Any]:
    record = await get_review_style(access.full_name)
    if not record:
        raise HTTPException(404, "review style not found")
    if record.get("status") == "running":
        record = await sync_review_style_run_status(access.full_name)
    return record


@router.put("/review-styles/{full_name:path}")
async def api_update_review_style_prompt(
    body: ReviewStylePromptUpdate,
    access: RepoAccess = REPO_FULL_NAME_ACCESS,
) -> dict[str, Any]:
    record = await get_review_style(access.full_name)
    if not record:
        raise HTTPException(404, "review style not found")
    return await set_custom_prompt(access.full_name, body.custom_prompt)


@router.post("/review-styles/{full_name:path}/analyze")
async def api_analyze_review_style(
    session: dict[str, Any] = SESSION,
    access: RepoAccess = REPO_FULL_NAME_ACCESS,
) -> dict[str, Any]:
    record = await get_review_style(access.full_name)
    if not record:
        record = await create_review_style(access.full_name, session["sub"])
    if record.get("status") == "running":
        record = await sync_review_style_run_status(access.full_name)
        if record.get("status") == "running":
            raise HTTPException(409, "analysis already running")
    return await start_bootstrap_analysis(
        access.full_name,
        github_token=access.token,
        created_by=session["sub"],
    )


@router.post("/review-styles/{full_name:path}/cancel")
async def api_cancel_review_style(
    access: RepoAccess = REPO_FULL_NAME_ACCESS,
) -> dict[str, Any]:
    record = await get_review_style(access.full_name)
    if not record:
        raise HTTPException(404, "review style not found")
    return await cancel_review_style_analysis(access.full_name)


@router.delete("/review-styles/{full_name:path}")
async def api_delete_review_style(
    access: RepoAccess = REPO_FULL_NAME_ACCESS,
) -> Response:
    record = await get_review_style(access.full_name)
    if not record:
        raise HTTPException(404, "review style not found")
    if record.get("status") == "running":
        await cancel_review_style_analysis(access.full_name)
    await remove_continual_cron(access.full_name)
    await delete_review_style(access.full_name)
    return Response(status_code=204)
