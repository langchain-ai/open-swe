"""Move Slack reaction, solo-thread, kickoff, DM origin, and view-suggestion state from
the LangGraph Store into PostgreSQL.

Runs through :func:`openswe.database.store_imports.run_store_import`, which
stops running it once a pass finds nothing left; a later release can delete
this module.

A Store record is copied, and deleted only once the copy has been in place for
a day: replicas of the previous release read and write it until their rollout
finishes. A Store record saved after the copy, by such a replica, replaces it.
View suggestions are the only records such a replica deletes, so a copied one
whose Store record is gone goes with it unless this release has saved it since.

Reaction event dedupe and reply freshness are not copied, only deleted: they
matter for minutes, and losing them during a rollout at worst handles a
redelivered reaction again or checks a reply for newer messages once more.
"""

import logging
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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
from openswe.slack.records import (
    KICKOFF_TTL,
    SlackDmOrigin,
    SlackKickoff,
    SlackReactionState,
    SlackSoloThread,
    SlackViewSuggestions,
)
from openswe.store import StoreEntry, delete_value, search_all_entries

logger = logging.getLogger(__name__)

VIEW_SUGGESTIONS_NAMESPACE = "slack_code_channel_view_suggestions"
_VIEW_SUGGESTIONS_KEY = "suggestions"
_DROPPED_NAMESPACES = ("slack_reaction_events", "slack_reply_freshness")
_BATCH = 500

type _Row = (
    type[SlackReactionState]
    | type[SlackSoloThread]
    | type[SlackKickoff]
    | type[SlackDmOrigin]
    | type[SlackViewSuggestions]
)
type _Location = tuple[str, ...]
type _Columns = dict[str, object]


async def run_slack_state_import() -> None:
    """Move Slack state in the background, so a backlog never delays startup."""
    try:
        await run_store_import("slack_state", import_store_slack_state)
    except Exception:
        logger.exception("Importing Slack state from the LangGraph Store failed")


async def import_store_slack_state() -> StoreImport:
    result = StoreImport()
    for kind in _KINDS:
        result += await kind.run()
    for namespace in _DROPPED_NAMESPACES:
        entries = await search_all_entries([namespace])
        for entry in entries:
            await delete_value(entry.namespace or [namespace], entry.key)
        result += StoreImport(moved=len(entries))
    return result


async def delete_store_view_suggestions(channel_id: str, view_id: str) -> None:
    """Delete a view's Store suggestions, so the import cannot bring back deleted ones."""
    await delete_value([VIEW_SUGGESTIONS_NAMESPACE, channel_id, view_id], _VIEW_SUGGESTIONS_KEY)


def _text(value: Mapping[str, object], key: str) -> str | None:
    text = value.get(key)
    return text if isinstance(text, str) and text else None


@dataclass
class _StoreRecords:
    """The Store records behind one PostgreSQL row, the latest saved one providing its columns."""

    columns: _Columns
    keys: list[tuple[list[str], str]] = field(default_factory=list)
    saved_at: datetime | None = None

    def add(self, entry: StoreEntry, columns: _Columns) -> None:
        self.keys.append((entry.namespace or [], entry.key))
        if not self.keys[:-1] or store_copy_is_newer(entry.updated_at, self.saved_at):
            self.columns, self.saved_at = columns, entry.updated_at

    async def settle(self, written_at: datetime | None) -> StoreImport | None:
        """Retire the records once their copy has settled; ``None`` when they need copying."""
        if written_at is None or store_copy_is_newer(self.saved_at, written_at):
            return None
        if not may_delete_store_copy(written_at):
            return StoreImport(waiting=1)
        for namespace, key in self.keys:
            await delete_value(namespace, key)
        return StoreImport(moved=len(self.keys))


@dataclass(frozen=True)
class _Kind:
    """One Store namespace and the table its records move to."""

    namespace: str
    row: _Row
    location: tuple[InstrumentedAttribute[str], ...]
    # The row's columns for a record in ``namespace + scope``, or ``None`` when unreadable.
    columns: Callable[[list[str], StoreEntry], _Columns | None]
    scope_depth: int = 1
    # Records saved longer ago than this are deleted instead of copied.
    ttl: timedelta | None = None

    async def run(self) -> StoreImport:
        result = StoreImport()
        copies: dict[_Location, _StoreRecords] = {}
        for entry in await search_all_entries([self.namespace]):
            namespace = entry.namespace or []
            scope = namespace[1:]
            columns = (
                self.columns(scope, entry)
                if namespace[:1] == [self.namespace]
                and len(scope) == self.scope_depth
                and all(scope)
                and entry.key
                else None
            )
            if columns is None:
                logger.warning(
                    "Leaving an unreadable Slack record in the Store",
                    extra={"store_namespace": "/".join(namespace), "store_key": entry.key},
                )
                continue
            if self.ttl is not None and _expired(entry.updated_at, self.ttl):
                await delete_value(namespace, entry.key)
                result += StoreImport(moved=1)
                continue
            location = tuple(str(columns[column.key]) for column in self.location)
            if (records := copies.get(location)) is None:
                records = copies[location] = _StoreRecords(columns)
            records.add(entry, columns)
        async with postgres.session() as session:
            written: dict[_Location, datetime | None] = {
                tuple(location): updated_at
                for *location, updated_at in await session.execute(
                    select(*self.location, self.row.updated_at)
                )
            }
        changed: list[_Columns] = []
        for location, records in copies.items():
            settled = await records.settle(written.get(location))
            if settled is None:
                changed.append({"id": uuid7(), **records.columns})
            else:
                result += settled
        excluded = insert(self.row).excluded
        keys = {column.key for column in self.location}
        update: dict[str, object] = (
            {name: getattr(excluded, name) for name in changed[0] if name not in {"id", *keys}}
            if changed
            else {}
        )
        for batch in _batches(changed):
            async with postgres.session() as session:
                await session.execute(
                    insert(self.row)
                    .values(batch)
                    .on_conflict_do_update(
                        index_elements=list(self.location),
                        set_={**update, "updated_at": func.clock_timestamp()},
                    )
                )
        result += StoreImport(moved=len(changed))
        if self.row is SlackViewSuggestions:
            result += StoreImport(moved=await _delete_views_gone_from_store(copies.keys()))
        return result


def _expired(saved_at: datetime | None, ttl: timedelta) -> bool:
    return saved_at is not None and saved_at + ttl <= datetime.now(UTC)


def _batches[T](rows: list[T]) -> Iterable[list[T]]:
    return (rows[start : start + _BATCH] for start in range(0, len(rows), _BATCH))


async def _delete_views_gone_from_store(present: Collection[_Location]) -> int:
    """Delete suggestions still as the import copied them, within the retire window, whose
    Store record is gone: a replica of the previous release deletes them only there."""
    copied_after = datetime.now(UTC) - RETIRE_STORE_COPY_AFTER
    row = SlackViewSuggestions
    async with postgres.session() as session:
        copies = await session.execute(
            select(row.id, row.channel_id, row.view_id).where(
                row.copied_from_store, row.updated_at > copied_after
            )
        )
        gone: list[UUID] = [
            row_id for row_id, channel, view in copies if (channel, view) not in present
        ]
        deleted = 0
        for batch in _batches(gone):
            removed = await session.scalars(
                delete(row).where(row.id.in_(batch), row.copied_from_store).returning(row.id)
            )
            deleted += len(removed.all())
    return deleted


def _reaction_state(scope: list[str], entry: StoreEntry) -> _Columns | None:
    user_id, message_ts = _text(entry.value, "user_id"), _text(entry.value, "message_ts")
    reactions = entry.value.get("reactions")
    if user_id is None or message_ts is None or not isinstance(reactions, list):
        return None
    return {
        "channel_id": scope[0],
        "message_ts": message_ts,
        "user_id": user_id,
        "reactions": sorted({reaction for reaction in reactions if isinstance(reaction, str)}),
    }


def _solo_thread(scope: list[str], entry: StoreEntry) -> _Columns | None:
    location: _Columns = {"channel_id": scope[0], "thread_ts": entry.key}
    if entry.value.get("blocked") is True:
        return {**location, "user_id": None, "last_seen": None}
    user_id, last_seen = _text(entry.value, "user_id"), entry.value.get("last_seen")
    if user_id is None or not isinstance(last_seen, int | float) or isinstance(last_seen, bool):
        return None
    return {**location, "user_id": user_id, "last_seen": float(last_seen)}


def _kickoff(scope: list[str], entry: StoreEntry) -> _Columns | None:
    kickoff_ts = _text(entry.value, "kickoff_ts")
    if kickoff_ts is None and entry.value.get("removed") is not True:
        return None
    return {
        "channel_id": scope[0],
        "thread_ts": entry.key,
        "kickoff_ts": kickoff_ts,
        "expires_at": (entry.updated_at or datetime.now(UTC)) + KICKOFF_TTL,
    }


def _dm_origin(scope: list[str], entry: StoreEntry) -> _Columns | None:
    channel_id, thread_ts = _text(entry.value, "channel_id"), _text(entry.value, "thread_ts")
    subject = entry.value.get("subject", "")
    if channel_id is None or thread_ts is None or not isinstance(subject, str):
        return None
    return {
        "dm_channel_id": scope[0],
        "message_ts": entry.key,
        "channel_id": channel_id,
        "thread_ts": thread_ts,
        "subject": subject,
    }


def _view_suggestions(scope: list[str], entry: StoreEntry) -> _Columns | None:
    actions = entry.value.get("actions")
    if entry.key != _VIEW_SUGGESTIONS_KEY or not isinstance(actions, dict):
        return None
    return {
        "channel_id": scope[0],
        "view_id": scope[1],
        "actions": actions,
        "copied_from_store": True,
    }


_KINDS = (
    _Kind(
        "slack_reaction_state",
        SlackReactionState,
        (SlackReactionState.channel_id, SlackReactionState.message_ts, SlackReactionState.user_id),
        _reaction_state,
    ),
    _Kind(
        "slack_solo_threads",
        SlackSoloThread,
        (SlackSoloThread.channel_id, SlackSoloThread.thread_ts),
        _solo_thread,
    ),
    _Kind(
        "slack_kickoff",
        SlackKickoff,
        (SlackKickoff.channel_id, SlackKickoff.thread_ts),
        _kickoff,
        ttl=KICKOFF_TTL,
    ),
    _Kind(
        "slack_dm_origin",
        SlackDmOrigin,
        (SlackDmOrigin.dm_channel_id, SlackDmOrigin.message_ts),
        _dm_origin,
    ),
    _Kind(
        VIEW_SUGGESTIONS_NAMESPACE,
        SlackViewSuggestions,
        (SlackViewSuggestions.channel_id, SlackViewSuggestions.view_id),
        _view_suggestions,
        scope_depth=2,
    ),
)
