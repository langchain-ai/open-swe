"""The transcript read API: one snapshot, one event stream, one tool output.

Nothing here calls LangGraph. A thread is authorized from the ``thread.metadata``
mirror with the same predicate the LangGraph-backed endpoints use, and a thread
with no ``thread`` row is simply not served by this API — the caller falls back
to the old read path.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from agent.dashboard.deps import SESSION_DEP

# The ``thread.metadata`` mirror exists so this predicate is reused unchanged.
from agent.threads.access import _assert_thread_readable  # noqa: PLC2701
from agent.transcript import listener
from agent.transcript.snapshot import (
    TranscriptAccess,
    load_access,
    load_events,
    load_snapshot,
    load_tool_output,
    measure_gap,
)
from agent.utils.timing import phase, server_timing_header

logger = logging.getLogger(__name__)

router = APIRouter(tags=["transcript"])

HEARTBEAT_SECONDS = 15.0
_REPLAY_PAGE = 500
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


async def _readable_transcript(thread_id: str, session: dict[str, Any]) -> TranscriptAccess:
    access = await load_access(thread_id)
    if access is None:
        raise HTTPException(404, "transcript_unavailable")
    _assert_thread_readable(access.metadata, session["sub"], session.get("email"))
    return access


def _frame(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


@router.get("/threads/{thread_id}/transcript")
async def api_get_thread_transcript(
    thread_id: str,
    session: dict[str, Any] = SESSION_DEP,
) -> JSONResponse:
    timings: dict[str, float] = {}
    with phase(timings, "auth"):
        await _readable_transcript(thread_id, session)
    with phase(timings, "query"):
        snapshot = await load_snapshot(thread_id)
    if snapshot is None:
        raise HTTPException(404, "transcript_unavailable")
    with phase(timings, "serialize"):
        payload = snapshot.model_dump(mode="json")
    return JSONResponse(payload, headers={"Server-Timing": server_timing_header(timings)})


@router.get("/threads/{thread_id}/transcript/events")
async def api_stream_thread_transcript(
    thread_id: str,
    after: int = 0,
    session: dict[str, Any] = SESSION_DEP,
) -> StreamingResponse:
    await _readable_transcript(thread_id, session)
    return StreamingResponse(
        _stream(thread_id, after),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.get("/threads/{thread_id}/transcript/tool-calls/{tool_call_id}/output")
async def api_get_thread_tool_output(
    thread_id: str,
    tool_call_id: str,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, str | bool]:
    await _readable_transcript(thread_id, session)
    output = await load_tool_output(thread_id, tool_call_id)
    if output is None:
        raise HTTPException(404, "tool call not found")
    return {"output": output.output, "truncated": output.truncated}


async def _stream(thread_id: str, after: int) -> AsyncIterator[str]:
    """Replay, then live. Subscribing happens first so nothing falls in the gap."""
    async with aclosing(listener.subscribe(thread_id)) as notifications:
        last_sent, replay = await _replay(thread_id, after)
        for chunk in replay:
            yield chunk
        yield _frame("synchronized", json.dumps({"version": last_sent}))
        pending: asyncio.Task[int] | None = None
        try:
            while True:
                if pending is None:
                    pending = asyncio.create_task(_next_version(notifications))
                done, _ = await asyncio.wait({pending}, timeout=HEARTBEAT_SECONDS)
                if not done:
                    yield ": ping\n\n"
                    continue
                finished, pending = pending, None
                try:
                    version = finished.result()
                except StopAsyncIteration:
                    return
                if version <= last_sent:
                    continue
                for event in await load_events(thread_id, after=last_sent, limit=_REPLAY_PAGE):
                    yield _frame("transcript", event.model_dump_json())
                    last_sent = event.version
        finally:
            if pending is not None:
                pending.cancel()


async def _next_version(notifications: AsyncIterator[int]) -> int:
    return await anext(notifications)


async def _replay(thread_id: str, after: int) -> tuple[int, list[str]]:
    """The frames that carry a subscriber from ``after`` to the head of the log.

    A cursor that is too far behind — or ahead of the head, which is what a
    reader of a recreated thread looks like — gets a snapshot instead of a
    replay it would spend longer applying than rendering.
    """
    gap = await measure_gap(thread_id, after)
    if gap.needs_snapshot(after):
        snapshot = await load_snapshot(thread_id)
        if snapshot is None:
            raise HTTPException(404, "transcript_unavailable")
        logger.info(
            "Serving a transcript snapshot instead of a replay",
            extra={
                "transcript": {
                    "thread_id": thread_id,
                    "after": after,
                    "head": gap.head,
                    "events": gap.events,
                    "payload_bytes": gap.payload_bytes,
                }
            },
        )
        return snapshot.version, [_frame("snapshot", snapshot.model_dump_json())]
    frames: list[str] = []
    last_sent = after
    while True:
        events = await load_events(thread_id, after=last_sent, limit=_REPLAY_PAGE)
        if not events:
            return last_sent, frames
        for event in events:
            frames.append(_frame("transcript", event.model_dump_json()))
            last_sent = event.version
        if len(events) < _REPLAY_PAGE:
            return last_sent, frames
