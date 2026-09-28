"""In-app browser for a thread's sandbox.

The dashboard's Browser surface drives a headless Chromium running inside the
thread's LangSmith sandbox over the Chrome DevTools Protocol. This module
brokers that connection: it authenticates the dashboard user with a
short-lived ticket, makes sure a Chromium is listening inside the sandbox,
tunnels its DevTools port to this process, and relays protocol frames
between the dashboard websocket and the browser. Chromium itself stays up
after the dashboard disconnects, so the agent and later sessions share it.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from functools import cache
from importlib import resources
from typing import Any, Literal
from urllib.parse import quote, urlsplit, urlunsplit

import httpx2
from fastapi import APIRouter, HTTPException, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError
from websockets.asyncio.client import connect as connect_websocket

from agent.config import ENV
from agent.dashboard.deps import SESSION_DEP
from agent.dashboard.oauth import (
    BROWSER_TICKET_AUDIENCE,
    decode_terminal_ticket,
    issue_terminal_ticket,
)
from agent.threads.handlers import get_dashboard_terminal_sandbox
from agent.utils.thread_ops import langgraph_url

logger = logging.getLogger(__name__)

router = APIRouter(tags=["threads"])

CLOUD_BROWSER_SUBPROTOCOL = "open-swe-browser"
DEVTOOLS_PORT = 9222
# Pinned so the browser build and its cache path are predictable across sandboxes.
PLAYWRIGHT_VERSION = "1.62.1"
INSTALL_HINT = f"npx --yes playwright@{PLAYWRIGHT_VERSION} install --with-deps chromium"

_CLOUD_BROWSER_SLOTS = asyncio.Semaphore(20)
_MAX_CLIENT_FRAME_BYTES = 1024 * 1024
# Screencast frames are base64 JPEGs; a 4K frame at high quality fits well within this.
_MAX_BROWSER_FRAME_BYTES = 32 * 1024 * 1024
_ENSURE_TIMEOUT_SECONDS = 60
_INSTALL_TIMEOUT_SECONDS = 15 * 60
_DEVTOOLS_VERSION_ATTEMPTS = 10
_DEVTOOLS_VERSION_RETRY_SECONDS = 0.3
_MAX_INSTALL_LINE_CHARS = 2000

Send = Callable[[dict[str, Any]], Awaitable[None]]


class BrowserProbe(BaseModel):
    """What the ensure script found inside the sandbox."""

    status: Literal["ready", "missing", "failed"]
    browser: str | None = None
    log: str | None = None


@cache
def _ensure_script() -> str:
    script = (
        resources.files("agent.resources")
        .joinpath("sandbox_browser_ensure.sh")
        .read_text(encoding="utf-8")
    )
    return script.replace("__PORT__", str(DEVTOOLS_PORT))


@cache
def _install_script() -> str:
    script = (
        resources.files("agent.resources")
        .joinpath("sandbox_browser_install.sh")
        .read_text(encoding="utf-8")
    )
    return script.replace("__PLAYWRIGHT_VERSION__", PLAYWRIGHT_VERSION)


def parse_browser_probe(output: str) -> BrowserProbe:
    """The ensure script prints its verdict as the last JSON line of stdout."""
    for line in reversed(output.strip().splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            return BrowserProbe.model_validate_json(candidate)
        except ValidationError:
            continue
    raise ValueError("browser probe produced no status line")


def rewrite_devtools_url(ws_url: str, local_port: int) -> str:
    """Point Chromium's advertised DevTools socket at the tunnel's local end.

    Only a `/devtools/...` path on the loopback tunnel is ever dialled, so the
    value Chromium reports cannot redirect this process anywhere else.
    """
    parsed = urlsplit(ws_url)
    if parsed.scheme != "ws" or not parsed.path.startswith("/devtools/"):
        raise ValueError("unexpected DevTools websocket URL")
    return urlunsplit(("ws", f"127.0.0.1:{local_port}", parsed.path, "", ""))


def classify_client_frame(
    raw: str,
) -> tuple[Literal["control", "cdp", "drop"], dict[str, Any] | None]:
    """Control messages carry a string `type`; DevTools messages never do."""
    if len(raw) > _MAX_CLIENT_FRAME_BYTES:
        return "drop", None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return "drop", None
    if not isinstance(payload, dict):
        return "drop", None
    if isinstance(payload.get("type"), str):
        return "control", payload
    if not isinstance(payload.get("id"), int) or not isinstance(payload.get("method"), str):
        return "drop", None
    return "cdp", payload


def _cloud_browser_websocket_url(thread_id: str) -> str:
    parsed = urlsplit(langgraph_url())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(500, "invalid LangGraph URL for the sandbox browser")
    path = f"{parsed.path.rstrip('/')}/dashboard/api/threads/{quote(thread_id, safe='')}/browser"
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


def _cloud_browser_session(websocket: WebSocket, thread_id: str) -> dict[str, Any]:
    offered = [
        value.strip()
        for value in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if value.strip()
    ]
    if len(offered) != 2 or offered[0] != CLOUD_BROWSER_SUBPROTOCOL:
        raise HTTPException(401, "invalid browser ticket")
    return decode_terminal_ticket(offered[1], thread_id=thread_id, audience=BROWSER_TICKET_AUDIENCE)


@router.post("/threads/{thread_id}/browser/connect")
async def api_thread_browser_connection(
    thread_id: str,
    response: Response,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, str]:
    # A browser is shell-equivalent access to the sandbox, so it follows the
    # terminal's rule: only the thread's owner, never an admin viewer.
    await get_dashboard_terminal_sandbox(thread_id, session["sub"], email=session.get("email"))
    response.headers["Cache-Control"] = "no-store"
    return {
        "url": _cloud_browser_websocket_url(thread_id),
        "protocol": CLOUD_BROWSER_SUBPROTOCOL,
        "ticket": issue_terminal_ticket(
            login=session["sub"],
            email=session.get("email"),
            thread_id=thread_id,
            audience=BROWSER_TICKET_AUDIENCE,
        ),
    }


async def _probe_browser(sandbox: Any) -> BrowserProbe:
    result = await sandbox.run(_ensure_script(), timeout=_ENSURE_TIMEOUT_SECONDS)
    return parse_browser_probe(result.stdout)


async def _await_install_request(websocket: WebSocket) -> None:
    """Block until the dashboard asks to install; other frames are ignored."""
    while True:
        kind, payload = classify_client_frame(await websocket.receive_text())
        if kind == "control" and payload is not None and payload.get("type") == "install":
            return


async def _install_browser(sandbox: Any, websocket: WebSocket, send: Send) -> bool:
    """Run the install script, streaming its output; returns whether it succeeded."""
    handle = await sandbox.run(
        _install_script(),
        timeout=_INSTALL_TIMEOUT_SECONDS,
        idle_timeout=-1,
        kill_on_disconnect=True,
        wait=False,
    )

    async def stream() -> int:
        async for chunk in handle:
            for line in chunk.data.splitlines():
                if line.strip():
                    await send({"type": "install-output", "data": line[:_MAX_INSTALL_LINE_CHARS]})
        return (await handle.result).exit_code

    async def watch_client() -> None:
        # Only a disconnect matters here; any other frame is discarded.
        while True:
            await websocket.receive_text()

    install_task = asyncio.create_task(stream())
    watch_task = asyncio.create_task(watch_client())
    done, pending = await asyncio.wait(
        {install_task, watch_task}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    if install_task not in done:
        await handle.kill()
        # The client went away mid-install; re-raise its disconnect.
        watch_task.result()
        return False
    exit_code = install_task.result()
    if exit_code != 0:
        await send(
            {"type": "error", "message": f"Chromium install failed (exit code {exit_code})."}
        )
        return False
    return True


async def _fetch_devtools_url(local_port: int) -> str:
    # Deliberately loopback: the tunnel's local listener, opened by this process.
    last_error: Exception | None = None
    async with httpx2.AsyncClient(timeout=10) as http:
        for _ in range(_DEVTOOLS_VERSION_ATTEMPTS):
            try:
                response = await http.get(f"http://127.0.0.1:{local_port}/json/version")
                response.raise_for_status()
                payload = response.json()
                ws_url = payload.get("webSocketDebuggerUrl") if isinstance(payload, dict) else None
                if not isinstance(ws_url, str):
                    raise ValueError("DevTools version response lacks webSocketDebuggerUrl")
                return rewrite_devtools_url(ws_url, local_port)
            except (httpx2.HTTPError, ValueError) as exc:
                last_error = exc
                await asyncio.sleep(_DEVTOOLS_VERSION_RETRY_SECONDS)
    raise RuntimeError("DevTools endpoint did not answer through the tunnel") from last_error


async def _relay(websocket: WebSocket, cdp: Any) -> None:
    async def browser_to_client() -> None:
        async for message in cdp:
            if isinstance(message, str):
                await websocket.send_text(message)

    async def client_to_browser() -> None:
        while True:
            raw = await websocket.receive_text()
            kind, _ = classify_client_frame(raw)
            if kind == "cdp":
                await cdp.send(raw)

    tasks = {asyncio.create_task(browser_to_client()), asyncio.create_task(client_to_browser())}
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        task.result()


async def _cloud_browser(websocket: WebSocket, thread_id: str, session: dict[str, Any]) -> None:
    if ENV.SANDBOX_TYPE.get() != "langsmith":
        await websocket.close(code=1008, reason="The sandbox browser requires a LangSmith sandbox")
        return
    try:
        sandbox_id, _ = await get_dashboard_terminal_sandbox(
            thread_id, session["sub"], email=session.get("email")
        )
    except HTTPException as exc:
        await websocket.close(code=1008, reason=str(exc.detail)[:123])
        return

    await websocket.accept(subprotocol=CLOUD_BROWSER_SUBPROTOCOL)
    try:
        await asyncio.wait_for(_CLOUD_BROWSER_SLOTS.acquire(), timeout=0.01)
    except TimeoutError:
        await websocket.close(code=1013, reason="Sandbox browser capacity reached")
        return

    async def send(message: dict[str, Any]) -> None:
        await websocket.send_text(json.dumps(message))

    client = None
    try:
        from agent.sandboxes.providers.langsmith import connect_async_langsmith_sandbox

        client, sandbox = await connect_async_langsmith_sandbox(sandbox_id)
        probe = await _probe_browser(sandbox)
        if probe.status == "missing":
            await send({"type": "browser-missing", "installCommand": INSTALL_HINT})
            await _await_install_request(websocket)
            await send({"type": "installing"})
            if not await _install_browser(sandbox, websocket, send):
                return
            probe = await _probe_browser(sandbox)
        if probe.status != "ready":
            logger.warning(
                "Sandbox browser did not start",
                extra={"thread": thread_id, "browser": probe.browser, "browser_log": probe.log},
            )
            await send({"type": "error", "message": "Chromium did not start inside the sandbox."})
            return
        async with await sandbox.tunnel(remote_port=DEVTOOLS_PORT, local_port=0) as tunnel:
            devtools_url = await _fetch_devtools_url(tunnel.local_port)
            async with connect_websocket(
                devtools_url,
                max_size=_MAX_BROWSER_FRAME_BYTES,
                compression=None,
                ping_interval=20,
            ) as cdp:
                await send({"type": "ready"})
                await _relay(websocket, cdp)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Sandbox browser bridge failed",
            extra={"thread": thread_id, "error_type": type(exc).__name__},
            exc_info=True,
        )
        try:
            await send({"type": "error", "message": "The sandbox browser disconnected."})
        except Exception:  # noqa: BLE001
            logger.debug("Could not notify the dashboard of the browser failure", exc_info=True)
    finally:
        if client is not None:
            await client.aclose()
        _CLOUD_BROWSER_SLOTS.release()


@router.websocket("/threads/{thread_id}/browser")
async def api_thread_browser(websocket: WebSocket, thread_id: str) -> None:
    try:
        session = _cloud_browser_session(websocket, thread_id)
    except HTTPException as exc:
        await websocket.close(code=1008, reason=str(exc.detail)[:123])
        return
    await _cloud_browser(websocket, thread_id, session)
