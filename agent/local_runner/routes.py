"""HTTP and WebSocket surface the desktop uses to offer its machine to a run."""

import asyncio
import logging
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from ..dashboard.oauth import (
    decode_runner_ticket,
    issue_runner_ticket,
    require_same_origin_for_mutations,
    require_session,
)
from ..store import now_ms
from ..utils.thread_ops import langgraph_url
from .broker import LocalRunnerConnection, WantedDevice, runner_broker, wanted_devices

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/dashboard/api/desktop/runner",
    tags=["local-runner"],
    dependencies=[Depends(require_same_origin_for_mutations)],
)

_SESSION_DEP = Depends(require_session)
_SUBPROTOCOL = "open-swe-runner"
# Each connection is an idle socket most of its life; the cap is about bounding
# a misbehaving client, not about concurrent work.
_RUNNER_SLOTS = asyncio.Semaphore(200)
MAX_DEVICE_ID_LENGTH = 64


def _validate_device_id(device_id: str) -> str:
    if not device_id or len(device_id) > MAX_DEVICE_ID_LENGTH or not device_id.isalnum():
        raise HTTPException(400, "invalid device id")
    return device_id


def _runner_websocket_url(device_id: str) -> str:
    parsed = urlsplit(langgraph_url())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(500, "invalid LangGraph URL for the local runner")
    path = f"{parsed.path.rstrip('/')}{router.prefix}/socket/{quote(device_id, safe='')}"
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


@router.post("/connect")
async def runner_connection(
    body: dict[str, Any],
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, str]:
    device_id = _validate_device_id(str(body.get("device_id") or ""))
    return {
        "url": _runner_websocket_url(device_id),
        "protocol": _SUBPROTOCOL,
        "ticket": issue_runner_ticket(
            login=session["sub"], email=session.get("email"), device_id=device_id
        ),
    }


@router.get("/wanted")
async def wanted_for_session(session: dict[str, Any] = _SESSION_DEP) -> dict[str, Any]:
    """Devices some replica is waiting to be introduced to.

    The desktop polls this and opens further sockets until one lands on the
    replica that asked, which is the only way a run and a workstation behind NAT
    can find each other across a load balancer.
    """
    records = await wanted_devices.search_all(filter={"login": session["sub"]})
    return {
        "devices": sorted(
            {record.device_id for record in records if not record.expired},
        ),
        "now_ms": now_ms(),
    }


def _runner_session(websocket: WebSocket, device_id: str) -> dict[str, Any]:
    offered = [
        value.strip()
        for value in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if value.strip()
    ]
    if len(offered) != 2 or offered[0] != _SUBPROTOCOL:
        raise HTTPException(401, "invalid runner ticket")
    return decode_runner_ticket(offered[1], device_id=device_id)


@router.websocket("/socket/{device_id}")
async def runner_socket(websocket: WebSocket, device_id: str) -> None:
    try:
        device_id = _validate_device_id(device_id)
        session = _runner_session(websocket, device_id)
    except HTTPException as exc:
        await websocket.close(code=1008, reason=str(exc.detail)[:123])
        return
    try:
        await asyncio.wait_for(_RUNNER_SLOTS.acquire(), timeout=0.01)
    except TimeoutError:
        await websocket.close(code=1013, reason="Local runner capacity reached")
        return

    await websocket.accept(subprotocol=_SUBPROTOCOL)
    connection = LocalRunnerConnection(session["sub"], device_id, websocket.send_json)
    runner_broker.register(connection)
    try:
        while True:
            reply = await websocket.receive_json()
            if isinstance(reply, dict):
                connection.resolve(reply)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Local runner socket failed for device %s", device_id)
    finally:
        runner_broker.unregister(connection)
        _RUNNER_SLOTS.release()


__all__ = ["WantedDevice", "router"]
