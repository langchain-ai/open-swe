"""Move per-repository agent instructions from the LangGraph Store into ``agent_instructions``.

Runs through :func:`openswe.database.store_imports.run_store_import`, which
stops running it once a pass finds nothing left; a later release can delete
this module.

Store keys were case-sensitive, so each repository's spellings are handled
together: the most recently saved is copied unless PostgreSQL's row was written
after it, and all of them are deleted once that row has been in place for a
day, so replicas of the previous release keep reading them until their rollout
ends. The row's ``updated_at`` is when it was copied, which is what a later
Store write is compared against.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from openswe.dashboard.agent_instructions import AGENT_INSTRUCTIONS, AgentInstructionsRow
from openswe.database import postgres
from openswe.database.store_imports import (
    StoreImport,
    may_delete_store_copy,
    store_copy_is_newer,
    timestamp,
)
from openswe.github.repo_names import normalize_repo_full_name
from openswe.github.repositories import Repository
from openswe.store import StoreEntry, delete_value, search_all_entries
from openswe.users.records import user_id_for_login
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

LEGACY_AGENT_INSTRUCTIONS_NAMESPACE: list[str] = ["agent_instructions"]
_UNKNOWN_TIME = datetime.min.replace(tzinfo=UTC)


async def delete_legacy_agent_instructions(key: str) -> None:
    """Delete every Store spelling of a repository, so the import cannot bring back deleted instructions."""
    for entry in await search_all_entries(LEGACY_AGENT_INSTRUCTIONS_NAMESPACE):
        if entry.key.lower() == key:
            await delete_value(LEGACY_AGENT_INSTRUCTIONS_NAMESPACE, entry.key)


async def import_store_agent_instructions() -> StoreImport:
    """Move agent instructions still in the LangGraph Store into PostgreSQL."""
    spellings: dict[str, list[StoreEntry]] = {}
    for entry in await search_all_entries(LEGACY_AGENT_INSTRUCTIONS_NAMESPACE):
        if (entry.namespace or LEGACY_AGENT_INSTRUCTIONS_NAMESPACE) != (
            LEGACY_AGENT_INSTRUCTIONS_NAMESPACE
        ):
            continue
        try:
            full_name = normalize_repo_full_name(entry.key)
        except ValueError:
            logger.warning(
                "Leaving unreadable agent instructions in the Store",
                extra={"store_key": entry.key},
            )
            continue
        spellings.setdefault(full_name.lower(), []).append(entry)
    result = StoreImport()
    for entries in spellings.values():
        result += await _import_spellings(entries)
    return result


async def _import_spellings(entries: list[StoreEntry]) -> StoreImport:
    latest = max(
        entries, key=lambda entry: timestamp(entry.value.get("updated_at")) or _UNKNOWN_TIME
    )
    full_name = normalize_repo_full_name(latest.key)
    existing = await AGENT_INSTRUCTIONS.get(full_name)
    copied_at = timestamp(existing.updated_at) if existing else None
    if existing is None or store_copy_is_newer(latest.value, copied_at):
        await _copy(full_name, latest.value)
        return StoreImport(moved=1)
    if not may_delete_store_copy(copied_at):
        return StoreImport(waiting=len(entries))
    for entry in entries:
        await delete_value(LEGACY_AGENT_INSTRUCTIONS_NAMESPACE, entry.key)
    return StoreImport(moved=len(entries))


async def _copy(full_name: str, value: JsonObject) -> None:
    created_by = value.get("created_by")
    instructions = value.get("instructions")
    values = {
        "instructions": instructions if isinstance(instructions, str) else "",
        "updated_at": func.clock_timestamp(),
    }
    async with postgres.session() as session:
        upsert = insert(AgentInstructionsRow).values(
            repository_id=await Repository.ensure_id(session, full_name),
            created_by=(
                user_id_for_login(created_by)
                if isinstance(created_by, str) and created_by
                else None
            ),
            created_at=timestamp(value.get("created_at")) or func.clock_timestamp(),
            **values,
        )
        await session.execute(
            upsert.on_conflict_do_update(
                index_elements=[AgentInstructionsRow.repository_id], set_=values
            )
        )
