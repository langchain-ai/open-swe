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

FollowUpBehavior = Literal["queue", "steer"]


class UserPreferencesUpdate(BaseModel):
    local_tracing_project: str | None = None
    default_workspace: str | None = None
    # What Enter does while a run is live: hold the message until the run ends,
    # or steer the live run. Omitted keeps the stored value.
    follow_up_behavior: FollowUpBehavior | None = None


def _normalize(record: dict[str, Any] | None) -> dict[str, Any]:
    project = (record or {}).get("local_tracing_project")
    workspace = (record or {}).get("default_workspace")
    normalized_workspace = (
        workspace.strip().lower() if isinstance(workspace, str) and workspace.strip() else None
    )
    return {
        "local_tracing_project": project if isinstance(project, str) and project.strip() else None,
        "default_workspace": normalized_workspace,
        "follow_up_behavior": (
            "steer" if (record or {}).get("follow_up_behavior") == "steer" else "queue"
        ),
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
        "local_tracing_project": update.local_tracing_project.strip()
        if update.local_tracing_project
        else None,
        "default_workspace": update.default_workspace.strip().lower()
        if update.default_workspace and update.default_workspace.strip()
        else None,
        "follow_up_behavior": (
            update.follow_up_behavior
            if update.follow_up_behavior is not None
            else existing.get("follow_up_behavior")
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
