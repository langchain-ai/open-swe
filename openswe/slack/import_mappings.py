"""Move Slack thread and run mappings from the LangGraph Store into PostgreSQL.

Runs through :func:`openswe.database.store_imports.run_store_import`, which
stops running it once a pass finds nothing left; a later release can delete
this module.

A Store record is copied, and deleted only once the copy has been in place for
a day: replicas of the previous release read and write these mappings on every
Slack event until their rollout finishes. A Store record saved after the copy,
by such a replica, replaces it, and a copy whose Store record such a replica
deleted goes with it unless this release has saved the mapping since.

A run's ``run:`` record and the ``thread:`` records naming it are one row; the
run a ``thread:`` record names was its Slack thread's current run when that
record was saved.
"""

import logging
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid7

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import InstrumentedAttribute

from openswe.database import postgres
from openswe.database.store_imports import (
    RETIRE_STORE_COPY_AFTER,
    StoreImport,
    may_delete_store_copy,
    run_store_import,
    store_copy_is_newer,
)
from openswe.slack.mappings import (
    MESSAGE_FIELDS,
    RUN_FIELDS,
    SlackMessageMapping,
    SlackRunMapping,
    SlackThreadMapping,
    merge_run_mapping,
)
from openswe.store import StoreEntry, delete_value, search_all_entries
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

THREAD_MAP_NAMESPACE = "slack_thread_map"
RUN_MAP_NAMESPACE = "slack_run_map"
_BATCH = 500
# A ``thread:`` record with no save time still names a current run, older than any other.
_UNKNOWN_TIME = datetime.fromtimestamp(0, UTC)

type _Key = tuple[str, str]
type _StoreKey = tuple[tuple[str, ...], str]
type _Row = type[SlackThreadMapping] | type[SlackRunMapping] | type[SlackMessageMapping]


async def run_slack_mapping_import() -> None:
    """Move mappings in the background, so a backlog never delays startup."""
    try:
        await run_store_import("slack_mappings", import_store_slack_mappings)
    except Exception:
        logger.exception("Importing Slack mappings from the LangGraph Store failed")


async def import_store_slack_mappings() -> StoreImport:
    return await _import_threads() + await _import_runs()


def _text(value: Mapping[str, object], key: str) -> str | None:
    text = value.get(key)
    return text if isinstance(text, str) and text else None


def _channel(entry: StoreEntry, namespace: str) -> str | None:
    match entry.namespace:
        case [prefix, channel] if prefix == namespace and channel and entry.key:
            return channel
    logger.warning(
        "Skipping unexpected Slack mapping in the Store",
        extra={"store_namespace": "/".join(entry.namespace or ()), "store_key": entry.key},
    )
    return None


@dataclass
class _StoreRecords:
    """The Store records behind one PostgreSQL row."""

    keys: list[_StoreKey] = field(default_factory=list)
    saved_at: datetime | None = None

    def add(self, entry: StoreEntry) -> None:
        self.keys.append((tuple(entry.namespace or ()), entry.key))
        if entry.updated_at is not None and (
            self.saved_at is None or entry.updated_at > self.saved_at
        ):
            self.saved_at = entry.updated_at

    async def settle(self, written_at: datetime | None) -> StoreImport | None:
        """Retire the records once their copy has settled; ``None`` when they need copying."""
        if written_at is None or store_copy_is_newer(self.saved_at, written_at):
            return None
        if not may_delete_store_copy(written_at):
            return StoreImport(waiting=1)
        for namespace, key in self.keys:
            await delete_value(namespace, key)
        return StoreImport(moved=len(self.keys))


@dataclass
class _StoreRun(_StoreRecords):
    """A run's ``run:`` record and the ``thread:`` records that name it."""

    own: JsonObject = field(default_factory=dict)
    thread: JsonObject = field(default_factory=dict)
    started_at: datetime | None = None

    def add_thread(self, entry: StoreEntry, thread_ts: str) -> None:
        self.add(entry)
        saved_at = entry.updated_at or _UNKNOWN_TIME
        if self.started_at is None or saved_at > self.started_at:
            self.started_at = saved_at
            self.thread = {"thread_ts": thread_ts, **entry.value}

    def row(self, channel_id: str, run_id: str) -> dict[str, object] | None:
        values = {name: _text(self.own, name) or _text(self.thread, name) for name in RUN_FIELDS}
        if values["thread_ts"] is None:
            return None
        return {
            "id": uuid7(),
            "channel_id": channel_id,
            "run_id": run_id,
            "started_at": self.started_at,
            "copied_from_store": True,
            **values,
        }


type _Copies = dict[_Key, tuple[_StoreRecords, dict[str, object]]]


async def _import_threads() -> StoreImport:
    copies: _Copies = {}
    for entry in await search_all_entries([THREAD_MAP_NAMESPACE]):
        if (channel := _channel(entry, THREAD_MAP_NAMESPACE)) is None:
            continue
        thread_id, nonce = _text(entry.value, "thread_id"), _text(entry.value, "nonce")
        if thread_id is None and nonce is None:
            logger.warning(
                "Leaving an unreadable Slack thread mapping in the Store",
                extra={"slack_channel": channel, "store_key": entry.key},
            )
            continue
        store = _StoreRecords()
        store.add(entry)
        copies[(channel, entry.key)] = (
            store,
            {
                "id": uuid7(),
                "channel_id": channel,
                "thread_ts": entry.key,
                "thread_id": thread_id,
                "detach_nonce": None if thread_id else nonce,
                "copied_from_store": True,
            },
        )
    excluded = insert(SlackThreadMapping).excluded
    return await _import(
        SlackThreadMapping,
        SlackThreadMapping.thread_ts,
        {
            "thread_id": excluded.thread_id,
            "detach_nonce": excluded.detach_nonce,
            "copied_from_store": True,
            "updated_at": func.clock_timestamp(),
        },
        copies,
    )


async def _import_runs() -> StoreImport:
    runs: dict[_Key, _StoreRun] = {}
    messages: _Copies = {}
    for entry in await search_all_entries([RUN_MAP_NAMESPACE]):
        if (channel := _channel(entry, RUN_MAP_NAMESPACE)) is None:
            continue
        kind, _, name = entry.key.partition(":")
        run_id = _text(entry.value, "run_id")
        match kind:
            case "thread" if run_id:
                runs.setdefault((channel, run_id), _StoreRun()).add_thread(entry, name)
            case "run" if name:
                run = runs.setdefault((channel, name), _StoreRun())
                run.add(entry)
                run.own = entry.value
            case "message" if run_id and (thread_ts := _text(entry.value, "thread_ts")):
                store = _StoreRecords()
                store.add(entry)
                messages[(channel, name)] = (
                    store,
                    {
                        "id": uuid7(),
                        "channel_id": channel,
                        "message_ts": name,
                        "run_id": run_id,
                        "thread_ts": thread_ts,
                        "triggering_user_id": _text(entry.value, "triggering_user_id"),
                        "agent_thread_id": _text(entry.value, "agent_thread_id"),
                        "copied_from_store": True,
                    },
                )
            case _:
                logger.warning(
                    "Leaving an unreadable Slack run mapping in the Store",
                    extra={"slack_channel": channel, "store_key": entry.key},
                )
    run_copies: _Copies = {}
    for (channel, run_id), run in runs.items():
        if (row := run.row(channel, run_id)) is None:
            logger.warning(
                "Leaving a Slack run mapping without a thread in the Store",
                extra={"slack_channel": channel, "run_id": run_id},
            )
        else:
            run_copies[(channel, run_id)] = (run, row)
    excluded = insert(SlackMessageMapping).excluded
    return await _import(
        SlackRunMapping, SlackRunMapping.run_id, merge_run_mapping(store_copy=True), run_copies
    ) + await _import(
        SlackMessageMapping,
        SlackMessageMapping.message_ts,
        {
            **{name: getattr(excluded, name) for name in MESSAGE_FIELDS},
            "copied_from_store": True,
            "updated_at": func.clock_timestamp(),
        },
        messages,
    )


async def _import(
    row: _Row, key: InstrumentedAttribute[str], update: Mapping[str, object], copies: _Copies
) -> StoreImport:
    """Copy each row whose Store records changed since PostgreSQL's, retiring settled ones."""
    async with postgres.session() as session:
        written: dict[_Key, datetime | None] = {
            (channel, name): updated_at
            for channel, name, updated_at in await session.execute(
                select(row.channel_id, key, row.updated_at)
            )
        }
    result, changed = StoreImport(), []
    for location, (store, values) in copies.items():
        settled = await store.settle(written.get(location))
        if settled is None:
            changed.append(values)
        else:
            result += settled
    for batch in _batches(changed):
        async with postgres.session() as session:
            await session.execute(
                insert(row)
                .values(batch)
                .on_conflict_do_update(index_elements=[row.channel_id, key], set_=update)
            )
    deleted = await _delete_copies_gone_from_store(row, key, copies.keys())
    return result + StoreImport(moved=len(changed) + deleted)


def _batches[T](rows: list[T]) -> Iterable[list[T]]:
    return (rows[start : start + _BATCH] for start in range(0, len(rows), _BATCH))


async def _delete_copies_gone_from_store(
    row: _Row, key: InstrumentedAttribute[str], present: Collection[_Key]
) -> int:
    """Delete rows still as the import copied them, within the retire window, whose Store record is gone.

    A replica of the previous release deletes a mapping only from the Store.
    """
    copied_after = datetime.now(UTC) - RETIRE_STORE_COPY_AFTER
    deleted = 0
    async with postgres.session() as session:
        copies = await session.execute(
            select(row.id, row.channel_id, key).where(
                row.copied_from_store, row.updated_at > copied_after
            )
        )
        gone: list[UUID] = [
            row_id for row_id, channel, name in copies if (channel, name) not in present
        ]
        for batch in _batches(gone):
            removed = await session.scalars(
                delete(row).where(row.id.in_(batch), row.copied_from_store).returning(row.id)
            )
            deleted += len(removed.all())
    return deleted
