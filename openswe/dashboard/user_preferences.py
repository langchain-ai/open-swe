"""Per-user dashboard preferences, keyed by the signed-in GitHub login."""

import logging
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import DashboardReadDep
from openswe.database import postgres
from openswe.users.records import UserRecords
from openswe.utils.time import now_iso

logger = logging.getLogger(__name__)

USER_PREFERENCES = UserRecords("dashboard_preferences")

ThreadVisibility = Literal["public", "private"]
FollowUpBehavior = Literal["queue", "steer"]


class UserPreferencesUpdate(BaseModel):
    default_visibility: ThreadVisibility
    # What Enter does while a run is live: hold the message until the run ends,
    # or steer the live run. Omitted keeps the stored value.
    follow_up_behavior: FollowUpBehavior | None = None
    # Open a thread's Changes panel when a run that edited files finishes.
    auto_show_changes: bool | None = None


def _normalize(record: dict[str, Any] | None) -> dict[str, Any]:
    visibility = (record or {}).get("default_visibility")
    return {
        "default_visibility": visibility if visibility in ("public", "private") else "private",
        "follow_up_behavior": (
            "queue" if (record or {}).get("follow_up_behavior") == "queue" else "steer"
        ),
        "auto_show_changes": (record or {}).get("auto_show_changes") is not False,
    }


async def get_user_preferences(
    login: str, *, session: AsyncSession | None = None, strict: bool = False
) -> dict[str, Any]:
    try:
        async with postgres.session(session) as db, db.begin_nested():
            record = await USER_PREFERENCES.get(login, session=db)
    except Exception:  # noqa: BLE001
        if strict:
            raise
        # Preferences only pick defaults; a store hiccup must not block a run.
        logger.debug("Could not load user preferences", exc_info=True)
        record = None
    return _normalize(record)


async def set_user_preferences(
    login: str, update: UserPreferencesUpdate, *, session: AsyncSession | None = None
) -> dict[str, Any]:
    async with postgres.session(session) as db:
        existing = await USER_PREFERENCES.get(login, session=db) or {}
        value = {
            **{key: value for key, value in existing.items() if key != "default_workspace"},
            "login": login,
            "default_visibility": update.default_visibility,
            "follow_up_behavior": (
                update.follow_up_behavior
                if update.follow_up_behavior is not None
                else existing.get("follow_up_behavior")
            ),
            "auto_show_changes": (
                update.auto_show_changes
                if update.auto_show_changes is not None
                else existing.get("auto_show_changes")
            ),
            "created_at": existing.get("created_at") or now_iso(),
            "updated_at": now_iso(),
        }
        await USER_PREFERENCES.put(login, value, session=db)
        return _normalize(value)


router = APIRouter(tags=["user-preferences"])


@router.get("/me/preferences")
async def api_get_my_preferences(
    reader: DashboardReadDep,
) -> dict[str, Any]:
    return await get_user_preferences(reader.principal.subject, session=reader.session)


@router.put("/me/preferences")
@audit_endpoint
async def api_put_my_preferences(
    body: UserPreferencesUpdate,
    reader: DashboardReadDep,
) -> dict[str, Any]:
    return await set_user_preferences(reader.principal.subject, body, session=reader.session)
