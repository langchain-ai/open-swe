"""Per-user dashboard preferences, keyed by the signed-in GitHub login."""

import logging
from typing import Any, Literal

from pydantic import BaseModel

from agent.store import get_value, now_iso, put_value

logger = logging.getLogger(__name__)

USER_PREFERENCES_NAMESPACE: list[str] = ["user_preferences"]

ThreadVisibility = Literal["public", "private"]


class UserPreferencesUpdate(BaseModel):
    default_visibility: ThreadVisibility


def _normalize(record: dict[str, Any] | None) -> dict[str, Any]:
    visibility = (record or {}).get("default_visibility")
    return {"default_visibility": visibility if visibility in ("public", "private") else "private"}


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
        "created_at": existing.get("created_at") or now_iso(),
        "updated_at": now_iso(),
    }
    await put_value(USER_PREFERENCES_NAMESPACE, login, value)
    return _normalize(value)
