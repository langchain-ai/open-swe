"""Move Slack threads, runs, messages, and view suggestions from the LangGraph Store into
PostgreSQL.

Two imports run through :func:`openswe.database.store_imports.run_store_import`,
which stops running each once a pass finds nothing left; a later release can
delete this module. ``slack_mappings`` moves thread bindings and run and
message mappings, as it has since those first moved; ``slack_state`` moves
solo-thread routing, kickoffs, DM origins, reactions, and view suggestions,
tracked apart so it runs even where ``slack_mappings`` already finished.

A Store record is copied, and deleted only once the copy has been in place for
a day: replicas of the previous release read and write these records on every
Slack event until their rollout finishes. Records saved after the copy, by such
a replica, replace it. Such a replica deletes run and message mappings and view
suggestions only from the Store, so a copy whose Store record is gone goes with
it unless this release has saved it since.

Several Store namespaces fill one row, each its own columns: a thread's
binding, solo-thread routing, and kickoff; a message's run mapping, DM origin,
and reactions. A namespace's record is compared with the row only while the
row holds that namespace's columns, so copying one namespace never makes
another's records look older than the row. A run's ``run:`` record and the
``thread:`` records that name it are one copy; the latest ``thread:`` record
made the run its thread's current one. Reaction event dedupe and reply
freshness are not copied, only deleted: they matter for minutes, and losing
them during a rollout at worst handles a redelivered reaction again or checks
a reply for newer messages once more.
"""

import logging
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid7

from sqlalchemy import (
    ColumnElement,
    case,
    delete,
    func,
    literal_column,
    select,
    true,
    tuple_,
    update,
)
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
    MESSAGE_FIELDS,
    RUN_FIELDS,
    SlackMessage,
    SlackRun,
    SlackThread,
    SlackViewSuggestion,
    merge_run,
)
from openswe.store import StoreEntry, delete_value, search_all_entries
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

VIEW_SUGGESTIONS_NAMESPACE = "slack_code_channel_view_suggestions"
_VIEW_SUGGESTIONS_KEY = "suggestions"
_DROPPED_NAMESPACES = ("slack_reaction_events", "slack_reply_freshness")
_BATCH = 500
# A ``thread:`` record with no save time still names a current run, older than any other.
_UNKNOWN_TIME = datetime.fromtimestamp(0, UTC)
_NO_REACTIONS = literal_column("'{}'::jsonb")

type _Key = tuple[str, str]
type _StoreKey = tuple[tuple[str, ...], str]
type _Row = type[SlackThread] | type[SlackRun] | type[SlackMessage] | type[SlackViewSuggestion]
type _Columns = dict[str, object]


async def run_slack_imports() -> None:
    """Move Slack records in the background, so a backlog never delays startup."""
    try:
        await run_store_import("slack_mappings", import_store_slack_mappings)
    except Exception:
        logger.exception("Importing Slack mappings from the LangGraph Store failed")
    try:
        await run_store_import("slack_state", import_store_slack_state)
    except Exception:
        logger.exception("Importing Slack state from the LangGraph Store failed")


async def import_store_slack_mappings() -> StoreImport:
    """Thread bindings, and the runs and bot messages of each Slack thread."""
    return await _import_bindings() + await _import_runs_and_messages()


async def import_store_slack_state() -> StoreImport:
    """Solo-thread routing, kickoffs, DM origins, reactions, and view suggestions."""
    return (
        await _import_solo_threads()
        + await _import_kickoffs()
        + await _import_dm_origins()
        + await _import_reactions()
        + await _import_view_suggestions()
        + await _purge_dropped()
    )


async def delete_store_view_suggestions(channel_id: str, view_id: str) -> None:
    """Delete a view's Store suggestions, so the import cannot bring back deleted ones."""
    await delete_value([VIEW_SUGGESTIONS_NAMESPACE, channel_id, view_id], _VIEW_SUGGESTIONS_KEY)


def _text(value: Mapping[str, object], key: str) -> str | None:
    text = value.get(key)
    return text if isinstance(text, str) and text else None


def _scope(entry: StoreEntry, namespace: str, depth: int = 1) -> list[str] | None:
    """The namespace parts after ``namespace``, or ``None`` for a record not shaped like one."""
    parts = entry.namespace or []
    scope = parts[1:]
    if parts[:1] == [namespace] and len(scope) == depth and all(scope) and entry.key:
        return scope
    _unreadable(entry)
    return None


def _unreadable(entry: StoreEntry) -> None:
    logger.warning(
        "Leaving an unreadable Slack record in the Store",
        extra={"store_namespace": "/".join(entry.namespace or ()), "store_key": entry.key},
    )


@dataclass
class _Copy:
    """The Store records behind one PostgreSQL row, and the columns they give it."""

    columns: _Columns = field(default_factory=dict)
    keys: list[_StoreKey] = field(default_factory=list)
    saved_at: datetime | None = None

    def add(self, entry: StoreEntry, columns: Mapping[str, object] | None = None) -> None:
        self.keys.append((tuple(entry.namespace or ()), entry.key))
        if entry.updated_at is not None and (
            self.saved_at is None or entry.updated_at > self.saved_at
        ):
            self.saved_at = entry.updated_at
        self.columns.update(columns or {})

    def row(self) -> _Columns | None:
        return self.columns

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
class _StoreRun(_Copy):
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

    def row(self) -> _Columns | None:
        values = {name: _text(self.own, name) or _text(self.thread, name) for name in RUN_FIELDS}
        if values["thread_ts"] is None:
            return None
        return {**values, "started_at": self.started_at}


async def _import_bindings() -> StoreImport:
    copies: dict[_Key, _Copy] = {}
    for entry in await search_all_entries(["slack_thread_map"]):
        if (scope := _scope(entry, "slack_thread_map")) is None:
            continue
        thread_id, nonce = _text(entry.value, "thread_id"), _text(entry.value, "nonce")
        if thread_id is None and nonce is None:
            _unreadable(entry)
            continue
        columns = {"thread_id": thread_id, "detach_nonce": None if thread_id else nonce}
        copies.setdefault((scope[0], entry.key), _Copy()).add(entry, columns)
    return await _import(
        SlackThread,
        SlackThread.thread_ts,
        copies,
        SlackThread.thread_id.is_not(None) | SlackThread.detach_nonce.is_not(None),
    )


async def _import_runs_and_messages() -> StoreImport:
    runs: dict[_Key, _StoreRun] = {}
    messages: dict[_Key, _Copy] = {}
    for entry in await search_all_entries(["slack_run_map"]):
        if (scope := _scope(entry, "slack_run_map")) is None:
            continue
        kind, _, name = entry.key.partition(":")
        run_id = _text(entry.value, "run_id")
        match kind:
            case "thread" if run_id:
                runs.setdefault((scope[0], run_id), _StoreRun()).add_thread(entry, name)
            case "run" if name:
                run = runs.setdefault((scope[0], name), _StoreRun())
                run.add(entry)
                run.own = entry.value
            case "message" if name and run_id and _text(entry.value, "thread_ts"):
                columns = {key: _text(entry.value, key) for key in MESSAGE_FIELDS}
                messages.setdefault((scope[0], name), _Copy()).add(entry, columns)
            case _:
                _unreadable(entry)
    result = await _import(SlackRun, SlackRun.run_id, runs, update_with=merge_run(store_copy=True))
    result += await _import(
        SlackMessage, SlackMessage.message_ts, messages, SlackMessage.run_id.is_not(None)
    )
    gone_runs = await _copies_gone_from_store(SlackRun, SlackRun.run_id, runs.keys())
    gone_messages = await _copies_gone_from_store(
        SlackMessage, SlackMessage.message_ts, messages.keys(), SlackMessage.run_id.is_not(None)
    )
    async with postgres.session() as session:
        for batch in _batches(gone_runs):
            await session.execute(
                delete(SlackRun).where(SlackRun.id.in_(batch), SlackRun.copied_from_store)
            )
        for batch in _batches(gone_messages):
            # A message keeps its DM origin and reactions, which only a Store write changes.
            await session.execute(
                delete(SlackMessage).where(
                    SlackMessage.id.in_(batch),
                    SlackMessage.copied_from_store,
                    SlackMessage.origin_channel_id.is_(None),
                    SlackMessage.reactions == _NO_REACTIONS,
                )
            )
            await session.execute(
                update(SlackMessage)
                .where(SlackMessage.id.in_(batch), SlackMessage.copied_from_store)
                .values(**dict.fromkeys(MESSAGE_FIELDS), updated_at=func.clock_timestamp())
            )
    return result + StoreImport(moved=len(gone_runs) + len(gone_messages))


async def _import_solo_threads() -> StoreImport:
    copies: dict[_Key, _Copy] = {}
    for entry in await search_all_entries(["slack_solo_threads"]):
        if (scope := _scope(entry, "slack_solo_threads")) is None:
            continue
        user_id, last_seen = _text(entry.value, "user_id"), entry.value.get("last_seen")
        if entry.value.get("blocked") is True:
            columns = {"solo_user_id": None, "solo_last_seen": None, "solo_blocked": True}
        elif user_id and isinstance(last_seen, int | float) and not isinstance(last_seen, bool):
            columns = {
                "solo_user_id": user_id,
                "solo_last_seen": float(last_seen),
                "solo_blocked": False,
            }
        else:
            _unreadable(entry)
            continue
        copies.setdefault((scope[0], entry.key), _Copy()).add(entry, columns)
    return await _import(
        SlackThread,
        SlackThread.thread_ts,
        copies,
        SlackThread.solo_blocked | SlackThread.solo_user_id.is_not(None),
    )


async def _import_kickoffs() -> StoreImport:
    result = StoreImport()
    copies: dict[_Key, _Copy] = {}
    for entry in await search_all_entries(["slack_kickoff"]):
        if (scope := _scope(entry, "slack_kickoff")) is None:
            continue
        kickoff_ts = _text(entry.value, "kickoff_ts")
        if kickoff_ts is None and entry.value.get("removed") is not True:
            _unreadable(entry)
            continue
        expires_at = (entry.updated_at or datetime.now(UTC)) + KICKOFF_TTL
        if expires_at <= datetime.now(UTC):
            await delete_value(entry.namespace or [], entry.key)
            result += StoreImport(moved=1)
            continue
        columns = {"kickoff_ts": kickoff_ts, "kickoff_expires_at": expires_at}
        copies.setdefault((scope[0], entry.key), _Copy()).add(entry, columns)
    return result + await _import(
        SlackThread, SlackThread.thread_ts, copies, SlackThread.kickoff_expires_at.is_not(None)
    )


async def _import_dm_origins() -> StoreImport:
    copies: dict[_Key, _Copy] = {}
    for entry in await search_all_entries(["slack_dm_origin"]):
        if (scope := _scope(entry, "slack_dm_origin")) is None:
            continue
        channel_id, thread_ts = _text(entry.value, "channel_id"), _text(entry.value, "thread_ts")
        subject = entry.value.get("subject", "")
        if channel_id is None or thread_ts is None or not isinstance(subject, str):
            _unreadable(entry)
            continue
        columns = {
            "origin_channel_id": channel_id,
            "origin_thread_ts": thread_ts,
            "origin_subject": subject,
        }
        copies.setdefault((scope[0], entry.key), _Copy()).add(entry, columns)
    return await _import(
        SlackMessage, SlackMessage.message_ts, copies, SlackMessage.origin_channel_id.is_not(None)
    )


async def _import_reactions() -> StoreImport:
    copies: dict[_Key, _Copy] = {}
    for entry in await search_all_entries(["slack_reaction_state"]):
        if (scope := _scope(entry, "slack_reaction_state")) is None:
            continue
        user_id, message_ts = _text(entry.value, "user_id"), _text(entry.value, "message_ts")
        reactions = entry.value.get("reactions")
        if user_id is None or message_ts is None or not isinstance(reactions, list):
            _unreadable(entry)
            continue
        copy = copies.setdefault((scope[0], message_ts), _Copy(columns={"reactions": {}}))
        copy.add(entry)
        by_user = copy.columns["reactions"]
        if isinstance(by_user, dict):
            by_user[user_id] = sorted({item for item in reactions if isinstance(item, str)})
    return await _import(
        SlackMessage,
        SlackMessage.message_ts,
        copies,
        SlackMessage.reactions != _NO_REACTIONS,
        update_with={
            "reactions": SlackMessage.reactions.op("||")(insert(SlackMessage).excluded.reactions)
        },
    )


async def _import_view_suggestions() -> StoreImport:
    copies: dict[_Key, _Copy] = {}
    for entry in await search_all_entries([VIEW_SUGGESTIONS_NAMESPACE]):
        if (scope := _scope(entry, VIEW_SUGGESTIONS_NAMESPACE, depth=2)) is None:
            continue
        actions = entry.value.get("actions")
        if entry.key != _VIEW_SUGGESTIONS_KEY or not isinstance(actions, dict):
            _unreadable(entry)
            continue
        copies.setdefault((scope[0], scope[1]), _Copy()).add(entry, {"actions": actions})
    result = await _import(SlackViewSuggestion, SlackViewSuggestion.view_id, copies)
    gone = await _copies_gone_from_store(
        SlackViewSuggestion, SlackViewSuggestion.view_id, copies.keys()
    )
    async with postgres.session() as session:
        for batch in _batches(gone):
            await session.execute(
                delete(SlackViewSuggestion).where(
                    SlackViewSuggestion.id.in_(batch), SlackViewSuggestion.copied_from_store
                )
            )
    return result + StoreImport(moved=len(gone))


async def _purge_dropped() -> StoreImport:
    moved = 0
    for namespace in _DROPPED_NAMESPACES:
        entries = await search_all_entries([namespace])
        for entry in entries:
            await delete_value(entry.namespace or [namespace], entry.key)
        moved += len(entries)
    return StoreImport(moved=moved)


async def _import(
    row: _Row,
    key: InstrumentedAttribute[str],
    copies: Mapping[_Key, _Copy],
    holds: ColumnElement[bool] | None = None,
    *,
    update_with: Mapping[str, ColumnElement[object] | bool] | None = None,
) -> StoreImport:
    """Copy each row whose Store records changed since PostgreSQL's, retiring settled ones.

    ``holds`` is whether a row has the columns these records give it; one that
    does not counts as never written. A copy sets only those columns, and
    ``update_with`` says how one merges into a row instead of replacing it.
    """
    async with postgres.session() as session:
        written: dict[_Key, datetime | None] = {
            (channel, name): updated_at
            for channel, name, updated_at in await session.execute(
                select(
                    row.channel_id,
                    key,
                    case((holds if holds is not None else true(), row.updated_at), else_=None),
                )
            )
        }
    result, changed, retired = StoreImport(), list[_Columns](), list[_Key]()
    for (channel_id, name), copy in copies.items():
        settled = await copy.settle(written.get((channel_id, name)))
        if settled is not None:
            result += settled
            if settled.moved:
                retired.append((channel_id, name))
        elif (columns := copy.row()) is None:
            logger.warning(
                "Leaving a Slack run mapping without a thread in the Store",
                extra={"slack_channel": channel_id, "run_id": name},
            )
        else:
            changed.append(
                {
                    **columns,
                    "id": uuid7(),
                    "channel_id": channel_id,
                    key.key: name,
                    "copied_from_store": True,
                }
            )
    if changed:
        excluded = insert(row).excluded
        names = [name for name in changed[0] if name not in {"id", "channel_id", key.key}]
        update_columns = {
            name: (update_with or {}).get(name, getattr(excluded, name)) for name in names
        }
        for batch in _batches(changed):
            async with postgres.session() as session:
                await session.execute(
                    insert(row)
                    .values(batch)
                    .on_conflict_do_update(
                        index_elements=[row.channel_id, key],
                        set_={**update_columns, "updated_at": func.clock_timestamp()},
                    )
                )
    # A row whose Store records are gone for good is no longer a copy of them.
    for batch in _batches(retired):
        async with postgres.session() as session:
            await session.execute(
                update(row)
                .where(row.copied_from_store, tuple_(row.channel_id, key).in_(batch))
                .values(copied_from_store=False, updated_at=func.clock_timestamp())
            )
    return result + StoreImport(moved=len(changed))


def _batches[T](rows: list[T]) -> Iterable[list[T]]:
    return (rows[start : start + _BATCH] for start in range(0, len(rows), _BATCH))


async def _copies_gone_from_store(
    row: _Row,
    key: InstrumentedAttribute[str],
    present: Collection[_Key],
    *where: ColumnElement[bool],
) -> list[UUID]:
    """Rows still as the import copied them, within the retire window, whose Store record is gone.

    A replica of the previous release deletes these records only from the Store.
    """
    copied_after = datetime.now(UTC) - RETIRE_STORE_COPY_AFTER
    async with postgres.session() as session:
        copies = await session.execute(
            select(row.id, row.channel_id, key).where(
                row.copied_from_store, row.updated_at > copied_after, *where
            )
        )
        return [row_id for row_id, channel, name in copies if (channel, name) not in present]
