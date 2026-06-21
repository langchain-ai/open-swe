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
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from langgraph_sdk import get_client
from pycrdt.websocket import WebsocketServer, YRoom
from pycrdt.websocket.websocket import HttpxWebsocket

from .oauth import COOKIE_NAME, _origin_of, allowed_dashboard_origins, decode_session
from .plan_store import load_yjs_snapshot, save_yjs_snapshot
from .thread_api import _thread_is_readable

logger = logging.getLogger(__name__)

collab_router = APIRouter(prefix="/dashboard/api/plan", tags=["plan-collab"])

_SNAPSHOT_DEBOUNCE_SECONDS = 1.5

_server: WebsocketServer | None = None
_dirty: set[str] = set()
# Per-room state, alive only while at least one reviewer is connected.
_connections: dict[str, int] = {}
_flushers: dict[str, asyncio.Task[None]] = {}
_subscriptions: dict[str, Any] = {}


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
            _subscriptions.clear()
            _connections.clear()
            _dirty.clear()


def _session_login(websocket: WebSocket) -> str | None:
    token = websocket.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        session = decode_session(token)
    except Exception:
        return None
    sub = session.get("sub") if isinstance(session, dict) else None
    return sub if isinstance(sub, str) and sub else None


def _origin_allowed(websocket: WebSocket) -> bool:
    """Same-origin (CSRF) gate for the WS handshake, mirroring the REST
    ``require_same_origin`` check: a no-op when no dashboard origins are
    configured (local/dev), otherwise the handshake ``Origin`` must be in the
    allowlist so a malicious site can't open a cross-site socket on the
    victim's cookie."""
    allowed = allowed_dashboard_origins()
    if not allowed:
        return True
    origin = _origin_of(websocket.headers.get("origin", "") or "")
    return bool(origin) and origin in allowed


async def _can_read_thread(thread_id: str) -> bool:
    """Same read gate as the plan REST API: only surfaced threads are joinable."""
    try:
        thread = await get_client().threads.get(thread_id)
    except Exception:
        return False
    metadata = (
        thread.get("metadata") if isinstance(thread, dict) else getattr(thread, "metadata", None)
    )
    return _thread_is_readable(metadata if isinstance(metadata, dict) else {})


async def _open_room(server: WebsocketServer, thread_id: str) -> None:
    """Seed the room from the store and start its snapshot flusher (idempotent
    while connected)."""
    if thread_id in _flushers:
        return
    room = await server.get_room(thread_id)
    snapshot = await load_yjs_snapshot(thread_id)
    if snapshot:
        try:
            room.ydoc.apply_update(snapshot)
        except Exception:
            logger.warning("Failed to seed plan doc %s from snapshot", thread_id, exc_info=True)

    def _on_change(_event: object) -> None:
        _dirty.add(thread_id)

    _subscriptions[thread_id] = room.ydoc.observe(_on_change)
    _flushers[thread_id] = asyncio.create_task(_flush_loop(thread_id, room))


async def _close_room(thread_id: str) -> None:
    """Tear down a room's flusher + observer once the last reviewer leaves."""
    task = _flushers.pop(thread_id, None)
    _subscriptions.pop(thread_id, None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    if _server is not None and thread_id in _server.rooms:
        await _persist(thread_id, _server.rooms[thread_id])
    _dirty.discard(thread_id)


async def _flush_loop(thread_id: str, room: YRoom) -> None:
    while True:
        await asyncio.sleep(_SNAPSHOT_DEBOUNCE_SECONDS)
        if thread_id in _dirty:
            _dirty.discard(thread_id)
            await _persist(thread_id, room)


async def _persist(thread_id: str, room: YRoom) -> None:
    try:
        await save_yjs_snapshot(thread_id, bytes(room.ydoc.get_update()))
    except Exception:
        logger.warning("Failed to snapshot plan doc %s", thread_id, exc_info=True)


@collab_router.websocket("/yjs/{thread_id}")
async def plan_yjs_socket(websocket: WebSocket, thread_id: str) -> None:
    if _session_login(websocket) is None:
        await websocket.close(code=4401)
        return
    if not _origin_allowed(websocket):
        await websocket.close(code=4403)
        return
    if not await _can_read_thread(thread_id):
        await websocket.close(code=4403)
        return
    if _server is None:
        await websocket.close(code=1013)  # try again later
        return

    await websocket.accept()
    await _open_room(_server, thread_id)
    _connections[thread_id] = _connections.get(thread_id, 0) + 1
    channel = HttpxWebsocket(websocket, thread_id)
    try:
        with contextlib.suppress(WebSocketDisconnect):
            await _server.serve(channel)
    finally:
        _connections[thread_id] = _connections.get(thread_id, 1) - 1
        if _connections.get(thread_id, 0) <= 0:
            _connections.pop(thread_id, None)
            await _close_room(thread_id)
