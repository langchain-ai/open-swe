"""The transcript read API: a windowed snapshot, older pages, an event stream, one tool output.

Nothing here calls LangGraph. A thread is authorized from the ``thread.metadata``
mirror with the same predicate the LangGraph-backed endpoints use, and a thread
with no ``thread`` row is simply not served by this API — the caller falls back
to the old read path.
"""

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse

from agent.dashboard.deps import SESSION_DEP

# The ``thread.metadata`` mirror exists so this predicate is reused unchanged.
from agent.threads.summary import assert_thread_readable
from agent.transcript import attachments, listener, tool_output
from agent.transcript.cursor import decode_turn_cursor
from agent.transcript.snapshot import (
    TranscriptTurnPage,
    load_access,
    load_events,
    load_snapshot,
    load_turn_page,
    measure_gap,
)
from agent.utils.timing import phase, server_timing_header

logger = logging.getLogger(__name__)

router = APIRouter(tags=["transcript"])

HEARTBEAT_SECONDS = 15.0
_REPLAY_PAGE = 500
ATTACHMENT_MAX_AGE_SECONDS = 86400
"""Attachment bytes never change once written, and are private to the thread."""

_UNSAFE_FILE_NAME = re.compile(r"[^A-Za-z0-9._-]")
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


async def _readable_transcript(thread_id: str, session: dict[str, Any]) -> None:
    """Raise unless ``session`` may read this thread's transcript."""
    metadata = await load_access(thread_id)
    if metadata is None:
        raise HTTPException(404, "transcript_unavailable")
    assert_thread_readable(metadata, session["sub"], session.get("email"))


async def _stream_access(
    thread_id: str, session: dict[str, Any]
) -> Literal["ok", "deleted", "revoked"]:
    """Whether a live subscriber may still be served this thread.

    ``assert_thread_readable`` answers a lost thread and a lost permission with
    the same 404, which a live reader has to tell apart: the metadata row is
    what says the transcript is gone, so the two are separated here.
    """
    metadata = await load_access(thread_id)
    if metadata is None:
        return "deleted"
    try:
        assert_thread_readable(metadata, session["sub"], session.get("email"))
    except HTTPException:
        return "revoked"
    return "ok"


def _frame(event: str, data: str) -> str:
    """One SSE frame.

    Two event names end a stream: ``deleted`` when the transcript is gone, and
    ``revoked`` when the caller has lost access to a thread it was reading.
    """
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


@router.get("/threads/{thread_id}/transcript/turns")
async def api_get_thread_transcript_turns(
    thread_id: str,
    before: str,
    limit: int | None = None,
    session: dict[str, Any] = SESSION_DEP,
) -> TranscriptTurnPage:
    """The page of turns immediately older than ``before``.

    A cursor that does not decode, or that was minted for another thread, is a
    client bug rather than a stale window: serving this thread's newest page
    instead would silently duplicate history the client already has, so it is
    rejected.
    """
    await _readable_transcript(thread_id, session)
    cursor = decode_turn_cursor(before)
    if cursor is None or cursor.thread_id != thread_id:
        raise HTTPException(400, "invalid_transcript_cursor")
    return await load_turn_page(thread_id, before=cursor, limit=limit)


@router.get("/threads/{thread_id}/transcript/events")
async def api_stream_thread_transcript(
    thread_id: str,
    after: int = 0,
    session: dict[str, Any] = SESSION_DEP,
) -> StreamingResponse:
    # Checked here as well as in the loop so a caller that may not read this
    # thread at all gets an HTTP error rather than a stream that ends at once.
    await _readable_transcript(thread_id, session)
    return StreamingResponse(
        _stream(thread_id, after, session),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.get("/threads/{thread_id}/transcript/tool-calls/{tool_call_id}/output")
async def api_get_thread_tool_output(
    thread_id: str,
    tool_call_id: str,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, str]:
    await _readable_transcript(thread_id, session)
    output = await tool_output.load(thread_id, tool_call_id)
    if output is None:
        raise HTTPException(404, "tool call not found")
    return {"output": output}


def _content_disposition(file_name: str | None) -> str:
    """``inline`` with a filename reduced to characters no header can misread."""
    safe = _UNSAFE_FILE_NAME.sub("_", file_name or "").strip("._-")[:100]
    return f'inline; filename="{safe}"' if safe else "inline"


@router.get("/threads/{thread_id}/transcript/attachments/{attachment_id}")
async def api_get_thread_attachment(
    thread_id: str,
    attachment_id: UUID,
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    """The bytes of one file attached to a message in this thread."""
    await _readable_transcript(thread_id, session)
    attachment = await attachments.load(thread_id, attachment_id)
    if attachment is None:
        raise HTTPException(404, "attachment not found")
    return Response(
        content=attachment.data,
        media_type=attachment.mime_type,
        headers={
            "Content-Disposition": _content_disposition(attachment.file_name),
            "X-Content-Type-Options": "nosniff",
            # An SVG opened directly would otherwise run script on this origin.
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            "Cache-Control": f"private, max-age={ATTACHMENT_MAX_AGE_SECONDS}, immutable",
        },
    )


async def _stream(thread_id: str, after: int, session: dict[str, Any]) -> AsyncIterator[str]:
    """Replay, then live. Subscribing happens first so nothing falls in the gap.

    A live stream outlives the authorization that opened it, so access is read
    again before every query: a thread flipped to private mid-stream ends the
    reader's stream with ``revoked`` instead of going on feeding it events.
    """
    async with aclosing(listener.subscribe(thread_id)) as notifications:
        replayed = await _replay(thread_id, after)
        if replayed is None:
            yield _frame("deleted", "{}")
            return
        last_sent, replay = replayed
        for chunk in replay:
            yield chunk
        yield _frame("synchronized", "{}")
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
                if version == listener.DELETED_VERSION:
                    yield _frame("deleted", "{}")
                    return
                if version <= last_sent:
                    continue
                # Drain, rather than ship one page per notification: a burst
                # longer than a page — or a notification ``listener.publish``
                # dropped for a slow subscriber — would otherwise leave the
                # remainder waiting on the next append that may never come,
                # and the ``turn.completed`` that ended the burst with it.
                while True:
                    access = await _stream_access(thread_id, session)
                    if access != "ok":
                        yield _frame(access, "{}")
                        return
                    events = await load_events(thread_id, after=last_sent, limit=_REPLAY_PAGE)
                    for event in events:
                        yield _frame("transcript", event.model_dump_json())
                        last_sent = event.version
                    if len(events) < _REPLAY_PAGE:
                        break
        finally:
            if pending is not None:
                # Let the cancelled ``anext`` leave the generator before
                # ``aclosing`` closes it, or aclose() raises "already running"
                # and the subscriber is never removed.
                pending.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pending


async def _next_version(notifications: AsyncIterator[int]) -> int:
    return await anext(notifications)


async def _replay(thread_id: str, after: int) -> tuple[int, list[str]] | None:
    """The frames that carry a subscriber from ``after`` to the head of the log.

    A cursor that is too far behind gets a snapshot instead of a replay it
    would spend longer applying than rendering.

    That ``snapshot`` frame carries the same newest window the snapshot
    endpoint serves, not the whole thread. A client merges it into what it
    holds rather than replacing: every turn older than the window has settled
    and is immutable, so older pages it already loaded stay correct and stay
    loaded.

    ``None`` means the transcript the subscriber was reading is gone: it was
    deleted between the authorization read and this one, or the cursor is past
    the head because the thread was recreated under the same id. Either way the
    stream ends instead of waiting.

    Each page is read in its own transaction, which is safe because versions
    are assigned under a per-thread advisory lock and therefore commit in
    order: the cursor only ever advances to a version actually read, so the
    live loop resumes from it without a gap or a repeat.
    """
    gap = await measure_gap(thread_id, after)
    # No head at all, or a cursor past it: the transcript the subscriber was
    # reading is gone rather than merely behind.
    if gap.head is None or after > gap.head:
        return None
    if gap.needs_snapshot:
        snapshot = await load_snapshot(thread_id)
        if snapshot is None:
            return None
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
