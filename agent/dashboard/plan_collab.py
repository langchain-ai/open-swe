"""Yjs collaboration server for the plan-review document.

A single :class:`WebsocketServer` (started for the app lifetime via
:func:`collab_lifespan`) relays Yjs sync/awareness frames between the reviewers
connected to a plan. Each plan is a room keyed by ``thread_id``; the room's
document is seeded from / snapshotted to the LangGraph store so a plan and its
comment threads survive a restart.

The browser connects with the standard ``y-websocket`` client, which appends the
room name to the provider URL — hence the ``/yjs/{thread_id}`` route shape.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pycrdt.websocket import WebsocketServer, YRoom
from pycrdt.websocket.websocket import HttpxWebsocket

from .oauth import COOKIE_NAME, decode_session
from .plan_store import load_yjs_snapshot, save_yjs_snapshot

logger = logging.getLogger(__name__)

collab_router = APIRouter(prefix="/dashboard/api/plan", tags=["plan-collab"])

_SNAPSHOT_DEBOUNCE_SECONDS = 1.5

_server: WebsocketServer | None = None
_loaded_rooms: set[str] = set()
_dirty: set[str] = set()
_flushers: dict[str, asyncio.Task[None]] = {}


@asynccontextmanager
async def collab_lifespan() -> AsyncIterator[None]:
    """Run the Yjs collaboration server for the app's lifetime."""
    global _server
    async with WebsocketServer(auto_clean_rooms=False) as server:
        _server = server
        try:
            yield
        finally:
            _server = None
            for task in _flushers.values():
                task.cancel()
            _flushers.clear()
            _loaded_rooms.clear()
            _dirty.clear()


def _authorized(websocket: WebSocket) -> bool:
    token = websocket.cookies.get(COOKIE_NAME)
    if not token:
        return False
    try:
        decode_session(token)
    except Exception:
        return False
    return True


async def _ensure_room_loaded(server: WebsocketServer, thread_id: str) -> None:
    """Seed a room's document from the store and start its snapshot flusher once."""
    if thread_id in _loaded_rooms:
        return
    _loaded_rooms.add(thread_id)
    room = await server.get_room(thread_id)
    snapshot = await load_yjs_snapshot(thread_id)
    if snapshot:
        try:
            room.ydoc.apply_update(snapshot)
        except Exception:
            logger.warning("Failed to seed plan doc %s from snapshot", thread_id, exc_info=True)

    def _on_change(_event: object) -> None:
        _dirty.add(thread_id)

    room.ydoc.observe(_on_change)
    _flushers[thread_id] = asyncio.create_task(_flush_loop(thread_id, room))


async def _flush_loop(thread_id: str, room: YRoom) -> None:
    try:
        while True:
            await asyncio.sleep(_SNAPSHOT_DEBOUNCE_SECONDS)
            if thread_id in _dirty:
                _dirty.discard(thread_id)
                await _persist(thread_id, room)
    except asyncio.CancelledError:
        if thread_id in _dirty:
            await _persist(thread_id, room)
        raise


async def _persist(thread_id: str, room: YRoom) -> None:
    try:
        await save_yjs_snapshot(thread_id, bytes(room.ydoc.get_update()))
    except Exception:
        logger.warning("Failed to snapshot plan doc %s", thread_id, exc_info=True)


@collab_router.websocket("/yjs/{thread_id}")
async def plan_yjs_socket(websocket: WebSocket, thread_id: str) -> None:
    if not _authorized(websocket):
        await websocket.close(code=4401)
        return
    if _server is None:
        await websocket.close(code=1013)  # try again later
        return

    await websocket.accept()
    await _ensure_room_loaded(_server, thread_id)
    channel = HttpxWebsocket(websocket, thread_id)
    with contextlib.suppress(WebSocketDisconnect):
        await _server.serve(channel)
