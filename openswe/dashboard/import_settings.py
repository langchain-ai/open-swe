"""Move instance and workspace settings from the LangGraph Store into ``settings_record``.

Runs through :func:`openswe.database.store_imports.run_store_import`, which
stops running it once a pass finds nothing left; a later release can delete
this module.

A Store record is copied, and deleted only once the copy has been in place for
a day. Replicas of the previous release read these records on every run to pick
models and toggles until their rollout finishes, and another replica of this
release can start minutes into it, so deleting on the next pass would hand them
the hardcoded defaults. A Store record saved after the copy, by such a replica,
replaces it.
"""

from sqlalchemy import select

from openswe.dashboard.settings_records import (
    INSTANCE_SETTINGS,
    WORKSPACE_SETTINGS,
    SettingsRecord,
    SettingsRecords,
)
from openswe.database import postgres
from openswe.database.store_imports import (
    StoreImport,
    may_delete_store_copy,
    store_copy_is_newer,
)
from openswe.store import Namespace, delete_value, search_all_entries
from openswe.utils.json_types import JsonObject

# The pre-workspaces "team settings" namespace holds the instance record under
# this key, and #2807 wrote each workspace's record beside it under its slug.
LEGACY_INSTANCE_NAMESPACE: list[str] = ["team_settings"]
LEGACY_INSTANCE_KEY = "default"
LEGACY_WORKSPACE_NAMESPACE: list[str] = ["workspace_settings"]


async def delete_legacy_workspace_settings(slug: str) -> None:
    """Delete a workspace's Store records, so the import cannot bring back deleted overrides."""
    await delete_value(LEGACY_WORKSPACE_NAMESPACE, slug)
    if slug != LEGACY_INSTANCE_KEY:
        await delete_value(LEGACY_INSTANCE_NAMESPACE, slug)


async def import_store_settings() -> StoreImport:
    """Move the instance record and every workspace's overrides out of the LangGraph Store.

    A workspace record under the legacy instance namespace only ever applied
    when the workspace had none of its own, so one shadowed that way is never
    copied, only deleted alongside the record that shadows it.
    """
    result = StoreImport()
    overridden: set[str] = set()
    for entry in await search_all_entries(LEGACY_WORKSPACE_NAMESPACE):
        if (entry.namespace or LEGACY_WORKSPACE_NAMESPACE) != LEGACY_WORKSPACE_NAMESPACE:
            continue
        overridden.add(entry.key)
        result += await _import(
            WORKSPACE_SETTINGS, entry.key, LEGACY_WORKSPACE_NAMESPACE, entry.key, entry.value
        )
    for entry in await search_all_entries(LEGACY_INSTANCE_NAMESPACE):
        if (entry.namespace or LEGACY_INSTANCE_NAMESPACE) != LEGACY_INSTANCE_NAMESPACE:
            continue
        if entry.key == LEGACY_INSTANCE_KEY:
            records, key, value = INSTANCE_SETTINGS, "", entry.value
        else:
            records, key = WORKSPACE_SETTINGS, entry.key
            value = None if entry.key in overridden else entry.value
        result += await _import(records, key, LEGACY_INSTANCE_NAMESPACE, entry.key, value)
    return result


async def _import(
    records: SettingsRecords,
    key: str,
    namespace: Namespace,
    store_key: str,
    value: JsonObject | None,
) -> StoreImport:
    """Copy a Store record saved after PostgreSQL's, and delete it once the copy has settled.

    ``value`` is ``None`` for a Store record that is never copied, only deleted.
    """
    async with postgres.session() as session:
        written_at = await session.scalar(
            select(SettingsRecord.updated_at).where(
                SettingsRecord.tier == records.tier, SettingsRecord.key == key
            )
        )
    if value is not None and (written_at is None or store_copy_is_newer(value, written_at)):
        await records.put(key, value)
        return StoreImport(moved=1)
    if not may_delete_store_copy(written_at):
        return StoreImport(waiting=1)
    await delete_value(namespace, store_key)
    return StoreImport(moved=1)
