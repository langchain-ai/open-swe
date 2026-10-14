"""Which Open SWE thread, runs, and bot messages belong to each Slack thread.

A Slack thread binds to one Open SWE thread. Detaching it leaves a nonce in
place of the binding, so resolving the location again names a fresh thread
instead of the one that moved away. Each run started for a Slack thread keeps
the last message it posted, and the run started most recently is the thread's
current one. Each bot message maps to the run that posted it, which is how
reactions and buttons on it find their run.
"""

import logging
from collections.abc import Mapping
from datetime import datetime
from uuid import UUID, uuid4, uuid7

from langgraph_sdk.client import LangGraphClient
from sqlalchemy import ColumnElement, ForeignKey, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.slack.client import SlackThreadMappingError, normalize_slack_location
from openswe.source_context import SlackThreadRef, SourceContext
from openswe.thread_ids import slack_thread_id

logger = logging.getLogger(__name__)


class SlackThreadMapping(Base):
    __tablename__ = "slack_thread_mapping"

    channel_id: Mapped[str]
    thread_ts: Mapped[str]
    thread_id: Mapped[str | None] = mapped_column(default=None)
    detach_nonce: Mapped[str | None] = mapped_column(default=None)
    copied_from_store: Mapped[bool] = mapped_column(server_default="false", default=False)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )


class SlackRunMapping(Base):
    """A run started for a Slack thread, or that posted in one, and its last message there."""

    __tablename__ = "slack_run_mapping"

    channel_id: Mapped[str]
    run_id: Mapped[str]
    thread_ts: Mapped[str]
    message_ts: Mapped[str | None] = mapped_column(default=None)
    triggering_user_id: Mapped[str | None] = mapped_column(default=None)
    trace_message_ts: Mapped[str | None] = mapped_column(default=None)
    agent_thread_id: Mapped[str | None] = mapped_column(default=None)
    # Unset for a run that only posted in the thread; the latest is the thread's current run.
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    copied_from_store: Mapped[bool] = mapped_column(server_default="false", default=False)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )


class SlackMessageMapping(Base):
    """The run behind a bot message, and who may act on it."""

    __tablename__ = "slack_message_mapping"

    channel_id: Mapped[str]
    message_ts: Mapped[str]
    run_id: Mapped[str]
    thread_ts: Mapped[str]
    triggering_user_id: Mapped[str | None] = mapped_column(default=None)
    agent_thread_id: Mapped[str | None] = mapped_column(default=None)
    copied_from_store: Mapped[bool] = mapped_column(server_default="false", default=False)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )


RUN_FIELDS = (
    "thread_ts",
    "message_ts",
    "triggering_user_id",
    "trace_message_ts",
    "agent_thread_id",
)
MESSAGE_FIELDS = ("run_id", "thread_ts", "triggering_user_id", "agent_thread_id")


def merge_run_mapping(*, store_copy: bool) -> dict[str, ColumnElement[object] | bool]:
    """How an upsert of ``slack_run_mapping`` updates a row: set fields replace, unset ones keep."""
    excluded = insert(SlackRunMapping).excluded
    return {
        **{
            field: func.coalesce(getattr(excluded, field), getattr(SlackRunMapping, field))
            for field in RUN_FIELDS
        },
        "started_at": func.greatest(excluded.started_at, SlackRunMapping.started_at),
        "copied_from_store": store_copy,
        "updated_at": func.clock_timestamp(),
    }


def _at_location(channel_id: str, thread_ts: str) -> ColumnElement[bool]:
    return (SlackThreadMapping.channel_id == channel_id) & (
        SlackThreadMapping.thread_ts == thread_ts
    )


async def lookup_slack_thread_id(channel_id: str, thread_ts: str) -> str | None:
    """The Open SWE thread bound to a Slack location."""
    channel, timestamp = normalize_slack_location(channel_id, thread_ts)
    async with postgres.session() as session:
        return await session.scalar(
            select(SlackThreadMapping.thread_id).where(_at_location(channel, timestamp))
        )


async def bind_slack_thread_id(channel_id: str, thread_ts: str, thread_id: str) -> str:
    """Bind a Slack location to an Open SWE thread unless another thread holds it."""
    channel, timestamp = normalize_slack_location(channel_id, thread_ts)
    normalized_thread_id = thread_id.strip() if isinstance(thread_id, str) else ""
    if not normalized_thread_id:
        raise SlackThreadMappingError("Open SWE thread ID is required")
    upsert = insert(SlackThreadMapping).values(
        id=uuid7(), channel_id=channel, thread_ts=timestamp, thread_id=normalized_thread_id
    )
    async with postgres.session() as session:
        bound = await session.scalar(
            upsert.on_conflict_do_update(
                index_elements=[SlackThreadMapping.channel_id, SlackThreadMapping.thread_ts],
                set_={
                    "thread_id": upsert.excluded.thread_id,
                    "copied_from_store": False,
                    "updated_at": func.clock_timestamp(),
                },
                where=SlackThreadMapping.thread_id.is_(None)
                | (SlackThreadMapping.thread_id == upsert.excluded.thread_id),
            ).returning(SlackThreadMapping.thread_id)
        )
    if bound is None:
        raise SlackThreadMappingError("Slack location is already mapped to another thread")
    return normalized_thread_id


def _thread_metadata_slack_location(thread: Mapping[str, object]) -> tuple[str, str] | None:
    metadata = thread.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    location = SourceContext.from_metadata(metadata).slack_location
    if location is None:
        return None
    try:
        return normalize_slack_location(*location)
    except SlackThreadMappingError:
        return None


async def resolve_slack_thread_id(
    langgraph_client: LangGraphClient, channel_id: str, thread_ts: str
) -> str:
    """The Open SWE thread bound to a Slack location, binding one first if none is."""
    channel, timestamp = normalize_slack_location(channel_id, thread_ts)
    async with postgres.session() as session:
        mapping = (
            await session.execute(
                select(SlackThreadMapping.thread_id, SlackThreadMapping.detach_nonce).where(
                    _at_location(channel, timestamp)
                )
            )
        ).first()
    if mapping is not None and mapping.thread_id:
        return mapping.thread_id

    matches = await langgraph_client.threads.search(
        metadata={
            "source_context": SourceContext(
                slack_thread=SlackThreadRef(channel_id=channel, thread_ts=timestamp)
            ).dump()
        },
        limit=2,
    )
    matching_ids = {
        candidate
        for thread in matches or []
        if isinstance(thread, Mapping)
        and _thread_metadata_slack_location(thread) == (channel, timestamp)
        and isinstance(candidate := thread.get("thread_id") or thread.get("id"), str)
        and candidate
    }
    if len(matching_ids) > 1:
        raise SlackThreadMappingError("Multiple Open SWE threads match this Slack location")

    candidate = next(
        iter(matching_ids),
        slack_thread_id(channel, timestamp, None if mapping is None else mapping.detach_nonce),
    )
    return await bind_slack_thread_id(channel, timestamp, candidate)


async def delete_slack_thread_associations(
    channel_id: str, thread_ts: str, *, expected_thread_id: str | None = None
) -> None:
    """Detach a Slack location from its thread and forget the runs and messages there.

    Nothing changes when ``expected_thread_id`` is given and another thread holds the location.
    """
    channel, timestamp = normalize_slack_location(channel_id, thread_ts)
    async with postgres.session() as session:
        mapped_thread_id = await session.scalar(
            select(SlackThreadMapping.thread_id)
            .where(_at_location(channel, timestamp))
            .with_for_update()
        )
        if expected_thread_id and mapped_thread_id and mapped_thread_id != expected_thread_id:
            return
        detach = insert(SlackThreadMapping).values(
            id=uuid7(), channel_id=channel, thread_ts=timestamp, detach_nonce=str(uuid4())
        )
        await session.execute(
            detach.on_conflict_do_update(
                index_elements=[SlackThreadMapping.channel_id, SlackThreadMapping.thread_ts],
                set_={
                    "thread_id": None,
                    "detach_nonce": detach.excluded.detach_nonce,
                    "copied_from_store": False,
                    "updated_at": func.clock_timestamp(),
                },
            )
        )
        await session.execute(
            delete(SlackRunMapping).where(
                SlackRunMapping.channel_id == channel, SlackRunMapping.thread_ts == timestamp
            )
        )
        await session.execute(
            delete(SlackMessageMapping).where(
                SlackMessageMapping.channel_id == channel,
                SlackMessageMapping.thread_ts == timestamp,
            )
        )


async def _current_run(
    session: AsyncSession, channel_id: str, thread_ts: str
) -> SlackRunMapping | None:
    return await session.scalar(
        select(SlackRunMapping)
        .where(
            SlackRunMapping.channel_id == channel_id,
            SlackRunMapping.thread_ts == thread_ts,
            SlackRunMapping.started_at.is_not(None),
        )
        .order_by(SlackRunMapping.started_at.desc())
        .limit(1)
    )


async def _run(session: AsyncSession, channel_id: str, run_id: str) -> SlackRunMapping | None:
    return await session.scalar(
        select(SlackRunMapping).where(
            SlackRunMapping.channel_id == channel_id, SlackRunMapping.run_id == run_id
        )
    )


async def _upsert_run(
    session: AsyncSession,
    channel_id: str,
    run_id: str,
    values: Mapping[str, str | None],
    *,
    start: bool = False,
) -> SlackRunMapping:
    """Save the run's set ``values``; ``start`` makes it the thread's current run."""
    upsert = insert(SlackRunMapping).values(
        id=uuid7(),
        channel_id=channel_id,
        run_id=run_id,
        started_at=func.clock_timestamp() if start else None,
        **values,
    )
    saved = await session.scalars(
        upsert.on_conflict_do_update(
            index_elements=[SlackRunMapping.channel_id, SlackRunMapping.run_id],
            set_=merge_run_mapping(store_copy=False),
        ).returning(SlackRunMapping),
        execution_options={"populate_existing": True},
    )
    return saved.one()


def _inherited(field: str, *runs: SlackRunMapping | None) -> str | None:
    return next((value for run in runs if run is not None and (value := getattr(run, field))), None)


async def _upsert_message(
    session: AsyncSession, channel_id: str, message_ts: str, values: Mapping[str, str | None]
) -> None:
    upsert = insert(SlackMessageMapping).values(
        id=uuid7(), channel_id=channel_id, message_ts=message_ts, **values
    )
    await session.execute(
        upsert.on_conflict_do_update(
            index_elements=[SlackMessageMapping.channel_id, SlackMessageMapping.message_ts],
            set_={
                **{field: getattr(upsert.excluded, field) for field in MESSAGE_FIELDS},
                "copied_from_store": False,
                "updated_at": func.clock_timestamp(),
            },
        )
    )


async def store_slack_run_mapping(
    channel_id: str,
    thread_ts: str,
    run_id: str,
    *,
    message_ts: str | None = None,
    triggering_user_id: str | None = None,
    trace_message_ts: str | None = None,
    agent_thread_id: str | None = None,
) -> None:
    """Make ``run_id`` the Slack thread's current run, keeping the thread's trace reply."""
    try:
        async with postgres.session() as session:
            if not trace_message_ts and (
                current := await _current_run(session, channel_id, thread_ts)
            ):
                trace_message_ts = current.trace_message_ts
            run = await _upsert_run(
                session,
                channel_id,
                run_id,
                {
                    "thread_ts": thread_ts,
                    "message_ts": message_ts,
                    "triggering_user_id": triggering_user_id,
                    "trace_message_ts": trace_message_ts,
                    "agent_thread_id": agent_thread_id,
                },
                start=True,
            )
            if message_ts:
                await _upsert_message(
                    session,
                    channel_id,
                    message_ts,
                    {
                        "run_id": run_id,
                        "thread_ts": thread_ts,
                        "triggering_user_id": run.triggering_user_id,
                        "agent_thread_id": run.agent_thread_id,
                    },
                )
    except Exception:
        logger.exception(
            "Failed to store Slack run mapping",
            extra={"slack_channel": channel_id, "slack_thread_ts": thread_ts, "run_id": run_id},
        )


async def store_slack_message_run_mapping(
    channel_id: str,
    thread_ts: str,
    message_ts: str,
    *,
    run_id: str | None = None,
    triggering_user_id: str | None = None,
) -> None:
    """Map a bot message to the run that posted it, the thread's current run by default."""
    try:
        async with postgres.session() as session:
            current = await _current_run(session, channel_id, thread_ts)
            run = await _run(session, channel_id, run_id) if run_id else current
            resolved_run_id = run_id or (current.run_id if current else None)
            if not resolved_run_id:
                logger.debug(
                    "No Slack run mapping found",
                    extra={"slack_channel": channel_id, "slack_thread_ts": thread_ts},
                )
                return
            # The run's own fields win; a run that never started here borrows the thread's.
            agent_thread_id = _inherited("agent_thread_id", run, current)
            triggering_user_id = triggering_user_id or _inherited(
                "triggering_user_id", run, current
            )
            await _upsert_message(
                session,
                channel_id,
                message_ts,
                {
                    "run_id": resolved_run_id,
                    "thread_ts": thread_ts,
                    "triggering_user_id": triggering_user_id,
                    "agent_thread_id": agent_thread_id,
                },
            )
            await _upsert_run(
                session,
                channel_id,
                resolved_run_id,
                {
                    "thread_ts": thread_ts,
                    "message_ts": message_ts,
                    "triggering_user_id": triggering_user_id,
                    "trace_message_ts": _inherited("trace_message_ts", run, current),
                    "agent_thread_id": agent_thread_id,
                },
            )
    except Exception:
        logger.exception(
            "Failed to store Slack message run mapping",
            extra={"slack_channel": channel_id, "slack_message_ts": message_ts},
        )


async def lookup_slack_run_message_mapping(channel_id: str, run_id: str) -> SlackRunMapping | None:
    """The run, with the latest Slack message it posted."""
    try:
        async with postgres.session() as session:
            return await _run(session, channel_id, run_id)
    except Exception:
        logger.exception(
            "Failed to look up Slack run mapping",
            extra={"slack_channel": channel_id, "run_id": run_id},
        )
        return None


async def lookup_slack_thread_run_mapping(
    channel_id: str, thread_ts: str
) -> SlackRunMapping | None:
    """The Slack thread's current run."""
    try:
        async with postgres.session() as session:
            return await _current_run(session, channel_id, thread_ts)
    except Exception:
        logger.exception(
            "Failed to look up Slack thread run mapping",
            extra={"slack_channel": channel_id, "slack_thread_ts": thread_ts},
        )
        return None


async def lookup_slack_run_mapping(channel_id: str, message_ts: str) -> SlackMessageMapping | None:
    """The run behind a bot message."""
    try:
        async with postgres.session() as session:
            return await session.scalar(
                select(SlackMessageMapping).where(
                    SlackMessageMapping.channel_id == channel_id,
                    SlackMessageMapping.message_ts == message_ts,
                )
            )
    except Exception:
        logger.exception(
            "Failed to look up Slack message run mapping",
            extra={"slack_channel": channel_id, "slack_message_ts": message_ts},
        )
        return None
