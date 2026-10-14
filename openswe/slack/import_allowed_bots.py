"""Move allowed Slack bots from the LangGraph Store into ``allowed_slack_bot``.

Runs through :func:`openswe.database.store_imports.run_store_import`, which
stops running it once a pass finds nothing left; a later release can delete
this module.

A Store record is copied, and deleted only once the copy has been in place for
a day: replicas of the previous release check these bots on every bot message
until their rollout finishes. A bot such a replica allows again after the copy
replaces it, and one such a replica revokes takes the copy with it.
"""

import logging
from datetime import UTC, datetime

from pydantic import ValidationError

from openswe.database.store_imports import (
    RETIRE_STORE_COPY_AFTER,
    StoreImport,
    may_delete_store_copy,
    store_copy_is_newer,
    timestamp,
)
from openswe.slack.allowed_bots import STORE_NAMESPACE, AllowedSlackBot
from openswe.store import delete_value, search_all_entries
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)


async def import_store_allowed_slack_bots() -> StoreImport:
    result = StoreImport()
    present: set[str] = set()
    for entry in await search_all_entries(STORE_NAMESPACE):
        if (entry.namespace or STORE_NAMESPACE) != STORE_NAMESPACE:
            continue
        present.add(entry.key)
        result += await _import(entry.key, entry.value)
    # Until the import retires a copy's Store record, a missing one was revoked there.
    deleted = await AllowedSlackBot.delete_store_copies(
        present, datetime.now(UTC) - RETIRE_STORE_COPY_AFTER
    )
    if deleted:
        logger.info("Deleted allowed Slack bots revoked in the Store", extra={"deleted": deleted})
    return result + StoreImport(moved=deleted)


async def _import(key: str, value: JsonObject) -> StoreImport:
    """Copy a Store record saved after PostgreSQL's, and delete it once the copy has settled."""
    try:
        bot = AllowedSlackBot.model_validate(
            {**value, "created_at": timestamp(value.get("created_at")) or datetime.now(UTC)}
        )
    except ValidationError:
        logger.warning(
            "Leaving an unreadable allowed Slack bot in the Store",
            extra={"store_key": key},
            exc_info=True,
        )
        return StoreImport()
    if bot.store_key != key:
        # Lookups only ever matched a record under its own team and bot.
        logger.warning(
            "Leaving a misfiled allowed Slack bot in the Store", extra={"store_key": key}
        )
        return StoreImport()
    written_at = await AllowedSlackBot.written_at(bot.team_id, bot.bot_id)
    if written_at is None or store_copy_is_newer(
        {"updated_at": value.get("created_at")}, written_at
    ):
        await bot.put_store_copy()
        return StoreImport(moved=1)
    if not may_delete_store_copy(written_at):
        return StoreImport(waiting=1)
    await delete_value(STORE_NAMESPACE, key)
    return StoreImport(moved=1)
