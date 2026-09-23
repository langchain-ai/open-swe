"""The CLI's side of the bridge: open, heartbeat, long-poll, reply, close.

Every route is scoped to the caller's ``Principal.sender_id`` (a person, an API
key or a CI workflow) and answers 404 for a bridge the caller does not own, so
an id belonging to someone else is indistinguishable from one that never existed.
"""

import asyncio
import logging
from typing import Self

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, model_validator

from agent.bridge import listener
from agent.bridge.constants import (
    ALIVE_THRESHOLD_SECONDS,
    CLOSED_EVENT,
    DEFAULT_CLAIM_LIMIT,
    DEFAULT_POLL_WAIT_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    MAX_CLAIM_LIMIT,
    MAX_POLL_WAIT_SECONDS,
)
from agent.bridge.protocol import JsonObject
from agent.bridge.store import BridgeStore, ClaimedRequest
from agent.database import postgres
from agent.threads.principals import PrincipalDep

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sandbox-bridge"])


class BridgeOpenBody(BaseModel):
    root_path: str
    hostname: str
    label: str | None = None
    bridge_id: str | None = None


class BridgeOpenResponse(BaseModel):
    bridge_id: str
    heartbeat_interval_seconds: int
    alive_threshold_seconds: int


class BridgeRequestsResponse(BaseModel):
    requests: list[ClaimedRequest]


class BridgeReplyBody(BaseModel):
    """The CLI's answer: a result, or the reason it could not produce one."""

    result: JsonObject | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.result is None) == (self.error is None):
            raise ValueError("send exactly one of result or error")
        return self


def _require_postgres() -> None:
    if not postgres.configured():
        raise HTTPException(503, "sandbox bridges require PostgreSQL")


async def _touch(bridge_id: str, owner_id: str) -> None:
    """Refresh the bridge's heartbeat, or say why the caller may not use it."""
    if await BridgeStore.heartbeat(bridge_id, owner_id=owner_id):
        return
    bridge = await BridgeStore.load(bridge_id, owner_id=owner_id)
    if bridge is None:
        raise HTTPException(404, "sandbox bridge not found")
    raise HTTPException(409, "sandbox bridge is closed; reopen it")


@router.post("/bridges")
async def api_open_bridge(
    body: BridgeOpenBody,
    principal: PrincipalDep,
) -> BridgeOpenResponse:
    _require_postgres()
    bridge = await BridgeStore.register(
        owner_id=principal.sender_id,
        hostname=body.hostname,
        root_path=body.root_path,
        label=body.label,
        bridge_id=body.bridge_id,
    )
    if bridge is None:
        raise HTTPException(404, "sandbox bridge not found")
    logger.info(
        "Opened a sandbox bridge",
        extra={
            "bridge_id": bridge.bridge_id,
            "bridge_owner": bridge.owner_id,
            "bridge_reopened": body.bridge_id is not None,
        },
    )
    return BridgeOpenResponse(
        bridge_id=bridge.bridge_id,
        heartbeat_interval_seconds=HEARTBEAT_INTERVAL_SECONDS,
        alive_threshold_seconds=ALIVE_THRESHOLD_SECONDS,
    )


@router.post("/bridges/{bridge_id}/heartbeat")
async def api_bridge_heartbeat(
    bridge_id: str,
    principal: PrincipalDep,
) -> Response:
    _require_postgres()
    await _touch(bridge_id, principal.sender_id)
    return Response(status_code=204)


@router.get("/bridges/{bridge_id}/requests")
async def api_claim_bridge_requests(
    bridge_id: str,
    principal: PrincipalDep,
    wait: int = Query(DEFAULT_POLL_WAIT_SECONDS, ge=0, le=MAX_POLL_WAIT_SECONDS),
    limit: int = Query(DEFAULT_CLAIM_LIMIT, ge=1, le=MAX_CLAIM_LIMIT),
) -> BridgeRequestsResponse:
    _require_postgres()
    await _touch(bridge_id, principal.sender_id)
    # Subscribing before the first claim is what closes the window in which a
    # request enqueued between the claim and the wait would go unnoticed.
    async with listener.subscribe(bridge_id) as events:
        requests = await BridgeStore.claim(bridge_id, limit=limit)
        if requests or wait <= 0:
            return BridgeRequestsResponse(requests=requests)
        try:
            async with asyncio.timeout(wait):
                async for _request_id, event in events:
                    if event == CLOSED_EVENT:
                        break
                    requests = await BridgeStore.claim(bridge_id, limit=limit)
                    if requests:
                        break
        except TimeoutError:
            pass
    return BridgeRequestsResponse(requests=requests)


@router.post("/bridges/{bridge_id}/requests/{request_id}")
async def api_answer_bridge_request(
    bridge_id: str,
    request_id: str,
    body: BridgeReplyBody,
    principal: PrincipalDep,
) -> Response:
    _require_postgres()
    await _touch(bridge_id, principal.sender_id)
    outcome = await BridgeStore.complete(
        bridge_id, request_id, result=body.result, error=body.error
    )
    if outcome == "missing":
        raise HTTPException(404, "sandbox bridge request not found")
    if outcome == "already_finished":
        raise HTTPException(409, "sandbox bridge request is already answered")
    return Response(status_code=204)


@router.delete("/bridges/{bridge_id}")
async def api_close_bridge(
    bridge_id: str,
    principal: PrincipalDep,
) -> Response:
    _require_postgres()
    if not await BridgeStore.close(bridge_id, owner_id=principal.sender_id):
        raise HTTPException(404, "sandbox bridge not found")
    logger.info("Closed a sandbox bridge", extra={"bridge_id": bridge_id})
    return Response(status_code=204)
