"""Workspace-wide settings: defaults, shared credentials, review opt-in."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from ...settings.enabled_repos import list_enabled_review_repos, set_review_repo_enabled
from ...settings.team_credentials import (
    DatadogCredentialsUpdate,
    LangSmithCredentialsUpdate,
    connect_datadog,
    connect_langsmith,
    disconnect_datadog,
    disconnect_langsmith,
    get_team_credentials_status,
)
from ...settings.team_settings import (
    TeamSettingsUpdate,
    TranscriptionSettingsUpdate,
    get_team_settings,
    update_team_transcription_model,
    upsert_team_settings,
)
from ..authz import ADMIN, SESSION

router = APIRouter()


@router.get("/team-settings")
async def api_get_team_settings(
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    return await get_team_settings()


@router.put("/team-settings/transcription")
async def api_put_transcription_settings(
    update: TranscriptionSettingsUpdate,
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, Any]:
    return await update_team_transcription_model(update.transcription_model)


@router.put("/team-settings")
async def api_put_team_settings(
    update: TeamSettingsUpdate,
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, Any]:
    return await upsert_team_settings(update)


@router.get("/team-credentials")
async def api_get_team_credentials(
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, Any]:
    return await get_team_credentials_status()


@router.put("/team-credentials/datadog")
async def api_connect_datadog(
    update: DatadogCredentialsUpdate,
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, Any]:
    return await connect_datadog(update)


@router.delete("/team-credentials/datadog")
async def api_disconnect_datadog(
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, Any]:
    return await disconnect_datadog()


@router.put("/team-credentials/langsmith")
async def api_connect_langsmith(
    update: LangSmithCredentialsUpdate,
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, Any]:
    return await connect_langsmith(update)


@router.delete("/team-credentials/langsmith")
async def api_disconnect_langsmith(
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, Any]:
    return await disconnect_langsmith()


class EnabledReviewRepoUpdate(BaseModel):
    full_name: str
    enabled: bool


@router.get("/enabled-review-repos")
async def api_list_enabled_review_repos(
    _session: dict[str, Any] = SESSION,
) -> dict[str, list[str]]:
    return {"repos": await list_enabled_review_repos()}


@router.put("/enabled-review-repos")
async def api_set_enabled_review_repo(
    update: EnabledReviewRepoUpdate,
    _admin: dict[str, Any] = ADMIN,
) -> dict[str, list[str]]:
    repos = await set_review_repo_enabled(update.full_name, update.enabled)
    return {"repos": repos}
