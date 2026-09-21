"""Per-user dashboard preferences, keyed by the signed-in GitHub login."""

import logging
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from agent.config import ENV
from agent.dashboard.deps import SESSION_DEP
from agent.store import get_value, now_iso, put_value

logger = logging.getLogger(__name__)

USER_PREFERENCES_NAMESPACE: list[str] = ["user_preferences"]

ThreadVisibility = Literal["public", "private"]


class UserPreferencesUpdate(BaseModel):
    default_visibility: ThreadVisibility
    local_tracing_project: str | None = None
    default_workspace: str | None = None
    # Opt-in while the transcript event log is rolling out: recording is not
    # optional, reading from it is. Omitted (a client built before the field
    # existed) keeps the stored value rather than switching the reader back.
    transcript_streaming: bool | None = None


def _normalize(record: dict[str, Any] | None) -> dict[str, Any]:
    visibility = (record or {}).get("default_visibility")
    project = (record or {}).get("local_tracing_project")
    workspace = (record or {}).get("default_workspace")
    normalized_workspace = (
        workspace.strip().lower() if isinstance(workspace, str) and workspace.strip() else None
    )
    return {
        "default_visibility": visibility if visibility in ("public", "private") else "private",
        "local_tracing_project": project if isinstance(project, str) and project.strip() else None,
        "default_workspace": normalized_workspace,
        "transcript_streaming": (record or {}).get("transcript_streaming") is True,
    }


async def get_user_preferences(login: str) -> dict[str, Any]:
    try:
        record = await get_value(USER_PREFERENCES_NAMESPACE, login)
    except Exception:  # noqa: BLE001
        # Preferences only pick defaults; a store hiccup must not block a run.
        logger.debug("Could not load user preferences", exc_info=True)
        record = None
    return _normalize(record)


async def set_user_preferences(login: str, update: UserPreferencesUpdate) -> dict[str, Any]:
    existing = await get_value(USER_PREFERENCES_NAMESPACE, login) or {}
    value = {
        **existing,
        "login": login,
        "default_visibility": update.default_visibility,
        "local_tracing_project": update.local_tracing_project.strip()
        if update.local_tracing_project
        else None,
        "default_workspace": update.default_workspace.strip().lower()
        if update.default_workspace and update.default_workspace.strip()
        else None,
        "transcript_streaming": (
            update.transcript_streaming
            if update.transcript_streaming is not None
            else existing.get("transcript_streaming") is True
        ),
        "created_at": existing.get("created_at") or now_iso(),
        "updated_at": now_iso(),
    }
    await put_value(USER_PREFERENCES_NAMESPACE, login, value)
    return _normalize(value)


router = APIRouter(tags=["user-preferences"])


@router.get("/me/preferences")
async def api_get_my_preferences(
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    return {
        **await get_user_preferences(session["sub"]),
        "default_local_tracing_project": ENV.LANGSMITH_PROJECT.get(),
    }


@router.put("/me/preferences")
async def api_put_my_preferences(
    body: UserPreferencesUpdate,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    return {
        **await set_user_preferences(session["sub"], body),
        "default_local_tracing_project": ENV.LANGSMITH_PROJECT.get(),
    }
