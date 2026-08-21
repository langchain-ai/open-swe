"""The browser ⇄ sandbox PTY bridge behind a thread's cloud terminal.

The browser cannot send an ``Authorization`` header on a WebSocket handshake,
so the ticket rides in the second subprotocol value: the connect endpoint mints
a short-lived, thread-bound one and the socket presents it here. Once open,
this is a plain two-pump relay — sandbox output out, keystrokes and resizes in —
capped by a process-wide slot count so one deployment cannot open unbounded
PTYs.
"""

import asyncio
import json
import logging
import posixpath
import shlex
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import HTTPException, WebSocket, WebSocketDisconnect

from ..config import is_langsmith_sandbox, langgraph_url
from .oauth import decode_terminal_ticket
from .threads.sandbox import get_dashboard_terminal_sandbox

logger = logging.getLogger(__name__)

CLOUD_TERMINAL_SUBPROTOCOL = "open-swe-terminal"
_CLOUD_TERMINAL_SLOTS = asyncio.Semaphore(20)


def cloud_terminal_websocket_url(thread_id: str) -> str:
    parsed = urlsplit(langgraph_url())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(500, "invalid LangGraph URL for cloud terminal")
    path = f"{parsed.path.rstrip('/')}/dashboard/api/threads/{quote(thread_id, safe='')}/terminal"
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


def cloud_terminal_session(websocket: WebSocket, thread_id: str) -> dict[str, Any]:
    offered = [
        value.strip()
        for value in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if value.strip()
    ]
    if len(offered) != 2 or offered[0] != CLOUD_TERMINAL_SUBPROTOCOL:
        raise HTTPException(401, "invalid terminal ticket")
    return decode_terminal_ticket(offered[1], thread_id=thread_id)


async def run_cloud_terminal(websocket: WebSocket, thread_id: str, session: dict[str, Any]) -> None:
    if not is_langsmith_sandbox():
        await websocket.close(code=1008, reason="Cloud terminal requires a LangSmith sandbox")
        return
    try:
        sandbox_id, repo_name = await get_dashboard_terminal_sandbox(
            thread_id, session["sub"], email=session.get("email")
        )
    except HTTPException as exc:
        await websocket.close(code=1008, reason=str(exc.detail)[:123])
        return

    await websocket.accept(subprotocol=CLOUD_TERMINAL_SUBPROTOCOL)
    client = handle = None
    try:
        await asyncio.wait_for(_CLOUD_TERMINAL_SLOTS.acquire(), timeout=0.01)
    except TimeoutError:
        await websocket.close(code=1013, reason="Cloud terminal capacity reached")
        return
    try:
        from ..integrations.langsmith import connect_async_langsmith_sandbox

        client, sandbox = await connect_async_langsmith_sandbox(sandbox_id)
        cwd = posixpath.join("/workspace", repo_name) if repo_name else "/workspace"
        if not (await sandbox.run(f"test -d {shlex.quote(cwd)}")).success:
            cwd = "/workspace"
        handle = await sandbox.run(
            "exec ${SHELL:-/bin/bash} -l",
            cwd=cwd,
            timeout=0,
            idle_timeout=300,
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
