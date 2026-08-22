"""Endpoints for a thread's cloud terminal: mint a ticket, then relay the PTY."""

from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket
from fastapi.responses import Response

from ..authz import SESSION
from ..cloud_terminal import (
    CLOUD_TERMINAL_SUBPROTOCOL,
    cloud_terminal_session,
    cloud_terminal_websocket_url,
    run_cloud_terminal,
)
from ..oauth import issue_terminal_ticket
from ..threads.sandbox import get_dashboard_terminal_sandbox

router = APIRouter()


@router.post("/threads/{thread_id}/terminal/connect")
async def api_thread_terminal_connection(
    thread_id: str,
    response: Response,
    session: dict[str, Any] = SESSION,
) -> dict[str, str]:
    await get_dashboard_terminal_sandbox(thread_id, session["sub"], email=session.get("email"))
    response.headers["Cache-Control"] = "no-store"
    return {
        "url": cloud_terminal_websocket_url(thread_id),
        "protocol": CLOUD_TERMINAL_SUBPROTOCOL,
        "ticket": issue_terminal_ticket(
            login=session["sub"], email=session.get("email"), thread_id=thread_id
        ),
    }


@router.websocket("/threads/{thread_id}/terminal")
async def api_thread_terminal(websocket: WebSocket, thread_id: str) -> None:
    try:
        session = cloud_terminal_session(websocket, thread_id)
    except HTTPException as exc:
        await websocket.close(code=1008, reason=str(exc.detail)[:123])
        return
    await run_cloud_terminal(websocket, thread_id, session)
