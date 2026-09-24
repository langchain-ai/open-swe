"""Pushing run starts and ends to live sidebars, so they need not poll.

The stream carries ``agent.threads.changes`` notifications, re-read and
authorised per viewer. A thread the viewer may not list is never named to it,
not even by id. Each tick costs one LangGraph search plus a run refresh per
changed thread, per connected viewer: acceptable at our scale, and the run
refreshes share the listing's concurrency bound.
"""

import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

from fastapi.encoders import jsonable_encoder

from agent.threads import changes
from agent.threads.listing import (
    _THREAD_LIST_SELECT,
    _summarize_threads,
    thread_has_participant,
)
from agent.threads.summary import (
    _is_automation_thread,
    _thread_id,
    _thread_metadata,
    thread_is_readable,
)
from agent.utils.json_types import ThreadLike
from agent.utils.thread_ops import langgraph_client

HEARTBEAT_SECONDS = 15.0
COALESCE_SECONDS = 0.05
MAX_BATCH = 256
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@dataclass(frozen=True)
class Viewer:
    login: str
    email: str | None
    include_all: bool
    """Every readable thread, as the admin-only ``all`` sidebar lists them."""

    def may_see(self, metadata: Mapping[str, Any]) -> bool:
        return thread_is_readable(metadata, self.login, self.email) and (
            self.include_all
            or _is_automation_thread(metadata)
            or thread_has_participant(metadata, self.login, email=self.email)
        )


def _frame(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


async def stream_thread_changes(viewer: Viewer) -> AsyncGenerator[str]:
    """``ready``, then ``thread-updated`` per visible changed thread and ``resync`` after a gap.

    Subscribing comes before ``ready`` so a client that refetches on ``ready``
    misses nothing in between.
    """
    async with changes.subscribe() as thread_ids:
        yield _frame("ready", "{}")
        pending: asyncio.Task[str] | None = None
        try:
            while True:
                if pending is None:
                    pending = asyncio.create_task(_next(thread_ids))
                done, _ = await asyncio.wait({pending}, timeout=HEARTBEAT_SECONDS)
                if not done:
                    yield ": ping\n\n"
                    continue
                finished, pending = pending, None
                batch = await _coalesce(finished.result(), thread_ids)
                if changes.RESYNC in batch:
                    yield _frame("resync", "{}")
                for summary in await _visible_summaries(
                    [thread_id for thread_id in batch if thread_id != changes.RESYNC], viewer
                ):
                    yield _frame(
                        "thread-updated", json.dumps(jsonable_encoder({"thread": summary}))
                    )
        finally:
            if pending is not None:
                pending.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pending


async def _next(thread_ids: AsyncIterator[str]) -> str:
    return await anext(thread_ids)


async def _coalesce(first: str, thread_ids: AsyncIterator[str]) -> list[str]:
    """``first`` and whatever else arrives within ``COALESCE_SECONDS``, each id once."""
    batch = {first: None}
    deadline = asyncio.get_running_loop().time() + COALESCE_SECONDS
    while len(batch) < MAX_BATCH:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        try:
            thread_id = await asyncio.wait_for(_next(thread_ids), remaining)
        except TimeoutError:
            break
        batch.pop(thread_id, None)
        batch[thread_id] = None
    return list(batch)


async def _visible_summaries(thread_ids: list[str], viewer: Viewer) -> list[dict[str, Any]]:
    """The ``/threads/page`` item for each thread the viewer may list; deleted ones drop out."""
    if not thread_ids:
        return []
    client = langgraph_client()
    found: list[ThreadLike] = await client.threads.search(
        ids=thread_ids, limit=len(thread_ids), select=_THREAD_LIST_SELECT
    )
    by_id = {
        thread_id: thread
        for thread in found
        if (thread_id := _thread_id(thread)) and viewer.may_see(_thread_metadata(thread))
    }
    return await _summarize_threads(
        client, [by_id[thread_id] for thread_id in thread_ids if thread_id in by_id]
    )
