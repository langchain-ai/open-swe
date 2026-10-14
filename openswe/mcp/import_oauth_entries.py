"""Move the MCP server's OAuth state from the LangGraph Store into PostgreSQL.

Runs through :func:`openswe.database.store_imports.run_store_import`, which
stops running it once a pass finds nothing left; a later release can delete
this module and the Store deletes in :class:`SealedStore`.

A Store entry is copied, and deleted only once the copy has been in place for a
day: replicas of the previous release answer OAuth steps from the Store until
their rollout finishes. A Store entry saved after the copy, by such a replica,
replaces it, and one such a replica deleted (a redeemed code, a revoked token)
takes the copy with it unless this release has saved the entry since. An
entry's save time is the ``created_at`` sealed inside it, which the key-value
store sets on every put; an entry without one never replaces a copy. Expired
entries are not copied; the Store's TTL sweeps them.
"""

import logging
from datetime import UTC, datetime

from openswe.database.store_imports import (
    RETIRE_STORE_COPY_AFTER,
    StoreImport,
    may_delete_store_copy,
)
from openswe.mcp.token_store import STORE_NAMESPACE, SealedStore, delete_store_copies
from openswe.store import delete_value, search_all_entries
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)


async def import_store_mcp_oauth_entries() -> StoreImport:
    """Move every MCP OAuth entry out of the LangGraph Store."""
    store = SealedStore()
    result = StoreImport()
    present: set[tuple[str, str]] = set()
    for entry in await search_all_entries(STORE_NAMESPACE):
        match entry.namespace:
            case [_, collection]:
                present.add((collection, entry.key))
                result += await _import(store, collection, entry.key, entry.value)
            case _:
                logger.warning(
                    "Leaving an MCP OAuth entry outside any collection in the Store",
                    extra={"store_key": entry.key},
                )
    # Until the import retires a copy's Store record, a missing one was deleted there.
    deleted = await delete_store_copies(present, datetime.now(UTC) - RETIRE_STORE_COPY_AFTER)
    if deleted:
        logger.info("Deleted MCP OAuth entries deleted in the Store", extra={"deleted": deleted})
    return result + StoreImport(moved=deleted)


async def _import(store: SealedStore, collection: str, key: str, value: JsonObject) -> StoreImport:
    """Copy a Store entry saved after PostgreSQL's, and delete it once the copy has settled."""
    sealed = value.get("sealed")
    if not isinstance(sealed, str) or (entry := store.open(sealed)) is None:
        # A key missing on this replica also leaves entries unreadable, so none is deleted.
        logger.warning(
            "Leaving an unreadable MCP OAuth entry in the Store",
            extra={"oauth_collection": collection, "store_key": key},
        )
        return StoreImport()
    if entry.is_expired:
        return StoreImport()
    written_at = await store.written_at(collection, key)
    if written_at is None or (entry.created_at is not None and entry.created_at > written_at):
        await store.put_sealed(collection, key, sealed, entry.expires_at, store_copy=True)
        return StoreImport(moved=1)
    if not may_delete_store_copy(written_at):
        return StoreImport(waiting=1)
    await delete_value((*STORE_NAMESPACE, collection), key)
    return StoreImport(moved=1)
