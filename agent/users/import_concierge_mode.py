"""Move the legacy ``dm_session_enabled`` profile flag into ``users.preferences``.

Concierge mode used to be ``dm_session_enabled`` on the person's ``["profiles"]``
Store record. Each startup copies every opted-in profile onto its ``users`` row
and then drops the flag from the record, so the import is idempotent and a
later release can delete this module. A choice already saved in Postgres wins,
and a profile whose login has no ``users`` row yet keeps its flag for the next
startup instead of being dropped.
"""

import logging

from pydantic import BaseModel, ConfigDict, ValidationError

from agent.store import put_value, search_all_values
from agent.users.models import User
from agent.users.preferences import UserPreferencesPatch

logger = logging.getLogger(__name__)

PROFILES_NAMESPACE: list[str] = ["profiles"]
LEGACY_FLAG = "dm_session_enabled"


class _LegacyProfile(BaseModel):
    model_config = ConfigDict(extra="allow")

    login: str


async def import_concierge_mode() -> int:
    """Copy every legacy opt-in into ``users.preferences``; returns how many were imported."""
    records = await search_all_values(PROFILES_NAMESPACE, filter={LEGACY_FLAG: True})
    imported = 0
    pending = 0
    for value in records:
        try:
            profile = _LegacyProfile.model_validate(value)
        except ValidationError:
            pending += 1
            logger.error("Skipping an unreadable profile with a concierge opt-in", exc_info=True)
            continue
        saved = await User.default_preferences(
            profile.login, UserPreferencesPatch(concierge_mode=True)
        )
        if saved is None:
            pending += 1
            logger.warning(
                "Concierge opt-in waits for its users row",
                extra={"github_login": profile.login},
            )
            continue
        await put_value(
            PROFILES_NAMESPACE,
            profile.login,
            {key: item for key, item in value.items() if key != LEGACY_FLAG},
        )
        imported += 1
    if records:
        logger.info(
            "Legacy concierge opt-ins processed",
            extra={"imported_users": imported, "pending_users": pending},
        )
    return imported
