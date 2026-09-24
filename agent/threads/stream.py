"""The sidebar's live feed: ``thread_index`` changes for one viewer, as SSE frames.

The shape follows the transcript stream: subscribe first, replay what the
client missed under one snapshot, then go live. Live changes are coalesced for
a short window, and the rows are re-read with the viewer's filters, so a frame
always reflects the row as committed, never the notification.

Frames:

- ``thread-upserted`` ``{"seq", "thread"}``: the row matches the viewer's
  filters; ``thread`` is the same summary a ``/threads/page`` item carries.
- ``thread-removed`` ``{"seq", "thread_id", "reason"}``: a row the viewer may
  hold no longer belongs in its list. ``reason`` is ``deleted`` (``seq`` is
  null), ``unreadable`` (the viewer lost access) or ``filtered`` (it was
  resolved, or otherwise left the filters).
- ``synchronized`` ``{"seq"}``: replay is done; ``seq`` is the head the client
  resumes from with ``after``.
- ``resync`` ``{"seq"}``: changes were missed (the replay was too long, the
  subscriber fell behind, or the listener reconnected). The client refetches
  its lists; the stream stays open.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncGenerator, Sequence
from typing import Literal

from fastapi.encoders import jsonable_encoder

from agent.database import postgres
from agent.threads import index_listener
from agent.threads.index_listener import IndexChange
from agent.threads.index_query import (
    ThreadIndexChange,
    ThreadListFilters,
    load_thread_index_changes,
    thread_index_head,
)
from agent.threads.listing import _index_summaries
from agent.threads.summary import _thread_metadata, thread_is_readable
from agent.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 15.0
COALESCE_SECONDS = 0.05
WINDOW_LIMIT = 512
REPLAY_LIMIT = 1000

type RemovalReason = Literal["deleted", "unreadable", "filtered"]


def _frame(event: str, data: JsonObject) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _removed(thread_id: str, seq: int | None, reason: RemovalReason) -> str:
    return _frame("thread-removed", {"seq": seq, "thread_id": thread_id, "reason": reason})


async def _change_frames(
    filters: ThreadListFilters,
    changes: Sequence[ThreadIndexChange],
    deleted: Sequence[str] = (),
) -> list[str]:
    """One frame per row, in ``seq`` order, after the deletions."""
    frames = [_removed(thread_id, None, "deleted") for thread_id in deleted]
    visible: list[ThreadIndexChange] = []
    outcomes: list[tuple[ThreadIndexChange, RemovalReason | None]] = []
    for change in changes:
        thread_id = change.thread.cursor.thread_id
        if not change.matches:
            outcomes.append((change, "filtered" if change.readable else "unreadable"))
        elif thread_is_readable(
            _thread_metadata(change.thread.thread), filters.login, filters.email
        ):
            visible.append(change)
            outcomes.append((change, None))
        else:
            logger.warning(
                "Thread index streamed a thread the viewer cannot read",
                extra={"thread_id": thread_id},
            )
            outcomes.append((change, "unreadable"))
    summaries = await _index_summaries(
        [change.thread for change in visible],
        filters.login,
        filters.email,
        expect_readable=False,
    )
    by_id = {str(summary["id"]): summary for summary in summaries}
    for change, reason in outcomes:
        thread_id = change.thread.cursor.thread_id
        summary = by_id.get(thread_id)
        if reason is None and summary is not None:
            frames.append(
                _frame("thread-upserted", {"seq": change.seq, "thread": jsonable_encoder(summary)})
            )
        else:
            frames.append(_removed(thread_id, change.seq, reason or "unreadable"))
    return frames


async def _replay(filters: ThreadListFilters, after: int) -> list[str]:
    """The frames that carry a client from ``after`` to the head.

    ``after=0`` is a client that has just fetched its lists, or is about to:
    it only needs the head. A gap longer than :data:`REPLAY_LIMIT`, or a cursor
    past the head (the index was rebuilt), is answered with ``resync``.
    """
    async with postgres.snapshot_transaction() as conn:
        head = await thread_index_head(conn=conn)
        changes: list[ThreadIndexChange] = []
        if 0 < after <= head:
            changes = await load_thread_index_changes(
                filters, after=after, limit=REPLAY_LIMIT + 1, conn=conn
            )
    synchronized = _frame("synchronized", {"seq": head})
    if after > head or len(changes) > REPLAY_LIMIT:
        return [_frame("resync", {"seq": head}), synchronized]
    return [*await _change_frames(filters, changes), synchronized]


async def _live_frames(filters: ThreadListFilters, window: Sequence[IndexChange]) -> list[str]:
    latest = {change.thread_id: change for change in window}
    async with postgres.snapshot_transaction() as conn:
        changes = await load_thread_index_changes(filters, thread_ids=list(latest), conn=conn)
    present = {change.thread.cursor.thread_id for change in changes}
    # A row missing after a seq notification is someone else's thread, not a
    # delete: a delete sends its own notification.
    deleted = [
        thread_id
        for thread_id, change in latest.items()
        if change.seq is None and thread_id not in present
    ]
    return await _change_frames(filters, changes, deleted)


async def _resync_frame() -> str:
    async with postgres.snapshot_transaction() as conn:
        head = await thread_index_head(conn=conn)
    return _frame("resync", {"seq": head})


async def stream_thread_index(filters: ThreadListFilters, after: int) -> AsyncGenerator[str]:
    """Replay from ``after``, then live changes, for the viewer ``filters`` describe.

    Every query runs in its own snapshot and every frame is yielded after it
    closes, so a slow socket never holds a transaction open.
    """
    async with index_listener.subscribe() as subscription:
        for frame in await _replay(filters, after):
            yield frame
        pending: asyncio.Task[IndexChange | None] | None = None
        try:
            while True:
                if pending is None:
                    pending = asyncio.create_task(subscription.next())
                done, _ = await asyncio.wait({pending}, timeout=HEARTBEAT_SECONDS)
                if not done:
                    yield ": ping\n\n"
                    continue
                first, pending = pending.result(), None
                await asyncio.sleep(COALESCE_SECONDS)
                window = [first, *subscription.pending(WINDOW_LIMIT - 1)]
                if subscription.stale:
                    # The client refetches everything, so this window is moot.
                    subscription.stale = False
                    yield await _resync_frame()
                    continue
                changes = [change for change in window if change is not None]
                if not changes:
                    continue
                for frame in await _live_frames(filters, changes):
                    yield frame
        finally:
            if pending is not None:
                pending.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pending
