"""Cloud terminal and sandbox service credentials for a thread's sandbox."""

import asyncio
import json
import logging
import posixpath
import shlex
from typing import Any

from fastapi import APIRouter, HTTPException, Response, WebSocket, WebSocketDisconnect

from openswe.audit_logs.middleware import audit_endpoint
from openswe.config import ENV
from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.oauth import TERMINAL_TICKET_AUDIENCE
from openswe.threads.handlers import get_dashboard_terminal_sandbox
from openswe.threads.sockets import ThreadSocket

logger = logging.getLogger(__name__)

router = APIRouter(tags=["threads"])

_CLOUD_TERMINAL_SLOTS = asyncio.Semaphore(20)
_CLOUD_TERMINAL_SUBPROTOCOL = "open-swe-terminal"
_TERMINAL_SOCKET = ThreadSocket(
    path="terminal", protocol=_CLOUD_TERMINAL_SUBPROTOCOL, audience=TERMINAL_TICKET_AUDIENCE
)


def _cloud_terminal_websocket_url(thread_id: str) -> str:
    return _TERMINAL_SOCKET.url(thread_id)


def _cloud_terminal_session(websocket: WebSocket, thread_id: str) -> dict[str, Any]:
    return _TERMINAL_SOCKET.session(websocket, thread_id)


@router.post("/threads/{thread_id}/terminal/connect")
@audit_endpoint
async def api_thread_terminal_connection(
    thread_id: str,
    response: Response,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, str]:
    await get_dashboard_terminal_sandbox(thread_id, session["sub"], email=session.get("email"))
    response.headers["Cache-Control"] = "no-store"
    return _TERMINAL_SOCKET.connection(thread_id, session)


async def _cloud_terminal(websocket: WebSocket, thread_id: str, session: dict[str, Any]) -> None:
    if ENV.SANDBOX_TYPE.get() != "langsmith":
        await websocket.close(code=1008, reason="Cloud terminal requires a LangSmith sandbox")
        return
    try:
        sandbox_id, repo_name = await get_dashboard_terminal_sandbox(
            thread_id, session["sub"], email=session.get("email")
        )
    except HTTPException as exc:
        await websocket.close(code=1008, reason=str(exc.detail)[:123])
        return

    await websocket.accept(subprotocol=_CLOUD_TERMINAL_SUBPROTOCOL)
    client = handle = None
    try:
        await asyncio.wait_for(_CLOUD_TERMINAL_SLOTS.acquire(), timeout=0.01)
    except TimeoutError:
        await websocket.close(code=1013, reason="Cloud terminal capacity reached")
        return
    try:
        from openswe.sandboxes.providers.langsmith import connect_async_langsmith_sandbox

        client, sandbox = await connect_async_langsmith_sandbox(sandbox_id)
        cwd = posixpath.join("/workspace", repo_name) if repo_name else "/workspace"
        if not (await sandbox.run(f"test -d {shlex.quote(cwd)}")).success:
            cwd = "/workspace"
        handle = await sandbox.run(
            "exec ${SHELL:-/bin/bash} -l",
            cwd=cwd,
            timeout=0,
            idle_timeout=-1,
            kill_on_disconnect=True,
            pty=True,
            wait=False,
        )

        async def output() -> None:
            assert handle is not None
            async for chunk in handle:
                await websocket.send_text(json.dumps({"type": "output", "data": chunk.data}))
            result = await handle.result
            await websocket.send_text(json.dumps({"type": "exit", "exitCode": result.exit_code}))

        async def input_() -> None:
            assert handle is not None
            while True:
                message = await websocket.receive_json()
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "input" and isinstance(message.get("data"), str):
                    data = message["data"]
                    if len(data.encode()) <= 64 * 1024:
                        await handle.send_input(data)
                elif message.get("type") == "resize":
                    cols, rows = message.get("cols"), message.get("rows")
                    if (
                        isinstance(cols, int)
                        and not isinstance(cols, bool)
                        and 1 <= cols <= 500
                        and isinstance(rows, int)
                        and not isinstance(rows, bool)
                        and 1 <= rows <= 500
                        and handle.pid is not None
                    ):
                        await sandbox.run(f"stty cols {cols} rows {rows} < /proc/{handle.pid}/fd/0")

        output_task = asyncio.create_task(output())
        input_task = asyncio.create_task(input_())
        done, pending = await asyncio.wait(
            {output_task, input_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cloud terminal failed for thread %s: %s", thread_id, type(exc).__name__)
        try:
            await websocket.send_text(
                json.dumps({"type": "error", "message": "Cloud terminal disconnected"})
            )
        except Exception:  # noqa: BLE001
            pass
    finally:
        if handle is not None:
            await handle.kill()
        if client is not None:
            await client.aclose()
        _CLOUD_TERMINAL_SLOTS.release()


@router.websocket("/threads/{thread_id}/terminal")
async def api_thread_terminal(websocket: WebSocket, thread_id: str) -> None:
    try:
        session = _cloud_terminal_session(websocket, thread_id)
    except HTTPException as exc:
        await websocket.close(code=1008, reason=str(exc.detail)[:123])
        return
    await _cloud_terminal(websocket, thread_id, session)
