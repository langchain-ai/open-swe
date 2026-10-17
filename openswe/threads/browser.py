"""The live view of the browser window a thread's agent opened.

LangSmith's own view pages refuse to be framed, so the dashboard watches a
window through this relay instead: the backend dials the window's live view
with the deployment's credentials, and passes JPEG frames out and, while a
person has taken the window over, their input in.
"""

import asyncio
import json
import logging
from typing import Any, Literal

import websockets
from fastapi import APIRouter, HTTPException, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from websockets.asyncio.client import ClientConnection

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.oauth import BROWSER_TICKET_AUDIENCE
from openswe.sandboxes.browsers import (
    BrowserGoneError,
    BrowserScope,
    BrowserTab,
    BrowserUnavailableError,
    BrowserWindow,
    ThreadBrowserWindow,
)
from openswe.sandboxes.providers.langsmith import sandbox_api_headers
from openswe.threads.handlers import get_promptable_thread_metadata
from openswe.threads.sockets import ThreadSocket
from openswe.ui_invalidations.topics import Topic

logger = logging.getLogger(__name__)

router = APIRouter(tags=["threads"])

_BROWSER_SOCKET = ThreadSocket(
    path="browser", protocol="open-swe-browser", audience=BROWSER_TICKET_AUDIENCE
)
_BROWSER_VIEW_SLOTS = asyncio.Semaphore(20)
# What a person's live view may send the window; browserd applies it only
# while they control the window.
_VIEWER_MESSAGE_TYPES = frozenset({"mouse", "key", "text", "navigate", "back", "forward", "reload"})
_VIEWER_MESSAGE_MAX_BYTES = 64 * 1024


class ThreadBrowser(BaseModel):
    """A thread's browser window as the dashboard shows it."""

    scope: BrowserScope
    controller: Literal["agent", "user"]
    reason: str
    tabs: list[BrowserTab]

    @classmethod
    def of(cls, record: ThreadBrowserWindow, window: BrowserWindow) -> ThreadBrowser:
        return cls(
            scope=record.scope,
            controller=window.controller,
            reason=window.reason,
            tabs=window.tabs,
        )


class BrowserControllerUpdate(BaseModel):
    controller: Literal["agent", "user"]


async def _thread_window(thread_id: str, login: str) -> ThreadBrowserWindow:
    record = ThreadBrowserWindow.of(await get_promptable_thread_metadata(thread_id, login))
    if record is None:
        raise HTTPException(404, "this thread has not opened a browser")
    return record


async def _window(record: ThreadBrowserWindow) -> BrowserWindow:
    try:
        return await record.langsmith.window(record.window)
    except BrowserGoneError as exc:
        raise HTTPException(410, "the thread's browser window is closed") from exc
    except BrowserUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/threads/{thread_id}/browser")
async def api_thread_browser(
    thread_id: str, session: dict[str, Any] = SESSION_DEP
) -> ThreadBrowser:
    record = await _thread_window(thread_id, session["sub"])
    return ThreadBrowser.of(record, await _window(record))


@router.post("/threads/{thread_id}/browser/controller")
@audit_endpoint
async def api_thread_browser_controller(
    thread_id: str,
    body: BrowserControllerUpdate,
    session: dict[str, Any] = SESSION_DEP,
) -> ThreadBrowser:
    """Take the window over from the agent, or hand it back."""
    record = await _thread_window(thread_id, session["sub"])
    await _window(record)
    window = await record.langsmith.set_controller(record.window, body.controller)
    await Topic.THREAD_BROWSERS.invalidate(key=thread_id)
    return ThreadBrowser.of(record, window)


@router.post("/threads/{thread_id}/browser/connect")
@audit_endpoint
async def api_thread_browser_connection(
    thread_id: str,
    response: Response,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, str]:
    await _thread_window(thread_id, session["sub"])
    response.headers["Cache-Control"] = "no-store"
    return _BROWSER_SOCKET.connection(thread_id, session)


@router.websocket("/threads/{thread_id}/browser")
async def api_thread_browser_view(websocket: WebSocket, thread_id: str, tab: str) -> None:
    try:
        session = _BROWSER_SOCKET.session(websocket, thread_id)
        record = await _thread_window(thread_id, session["sub"])
        upstream_url = await record.langsmith.view_socket_url(record.window, tab)
    except HTTPException as exc:
        await websocket.close(code=1008, reason=str(exc.detail)[:123])
        return
    except (BrowserGoneError, BrowserUnavailableError) as exc:
        logger.info(
            "Thread browser view unavailable",
            extra={"thread_id": thread_id, "error": type(exc).__name__},
        )
        await websocket.close(code=1008, reason="The thread's browser is unavailable")
        return
    try:
        await asyncio.wait_for(_BROWSER_VIEW_SLOTS.acquire(), timeout=0.01)
    except TimeoutError:
        await websocket.close(code=1013, reason="Browser view capacity reached")
        return
    try:
        await websocket.accept(subprotocol=_BROWSER_SOCKET.protocol)
        async with websockets.connect(
            upstream_url, additional_headers=sandbox_api_headers(), max_size=None
        ) as upstream:
            await _relay(websocket, upstream)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Thread browser view failed",
            extra={"thread_id": thread_id, "error": type(exc).__name__},
        )
        try:
            await websocket.send_text(
                json.dumps({"type": "error", "message": "Browser view disconnected"})
            )
            await websocket.close()
        except Exception:  # noqa: BLE001
            logger.debug("Could not report a closed browser view", exc_info=True)
    finally:
        _BROWSER_VIEW_SLOTS.release()


async def _relay(viewer: WebSocket, upstream: ClientConnection) -> None:
    async def frames() -> None:
        async for message in upstream:
            if isinstance(message, str):
                await viewer.send_text(message)

    async def input_() -> None:
        while True:
            message = await viewer.receive_text()
            if len(message.encode()) > _VIEWER_MESSAGE_MAX_BYTES:
                continue
            try:
                parsed = json.loads(message)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and parsed.get("type") in _VIEWER_MESSAGE_TYPES:
                await upstream.send(message)

    tasks = {asyncio.create_task(frames()), asyncio.create_task(input_())}
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        task.result()
