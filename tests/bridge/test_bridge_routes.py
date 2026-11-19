"""The wire contract the desktop is built against, called the way FastAPI calls it."""

import asyncio

import pytest
from fastapi import HTTPException

from openswe.bridge.protocol import JsonObject
from openswe.bridge.routes import (
    BridgeClaimBody,
    BridgeOpenBody,
    BridgeReplyBody,
    api_answer_bridge_request,
    api_bridge_heartbeat,
    api_claim_bridge_requests,
    api_close_bridge,
    api_open_bridge,
)
from openswe.bridge.store import BridgeStore
from openswe.threads.principals import Principal

OWNER = Principal.of_login("test-user")
INTRUDER = Principal.of_login("someone-else")


def _body(bridge_id: str | None = None) -> BridgeOpenBody:
    return BridgeOpenBody(
        client="desktop",
        root_path="local-access://approved-actions",
        hostname="laptop.local",
        bridge_id=bridge_id,
    )


async def _open() -> str:
    opened = await api_open_bridge(_body(), principal=OWNER)
    return opened.bridge_id


async def _enqueue(bridge_id: str, path: str = "~/notes.txt") -> str:
    params: JsonObject = {"operation": "read", "path": path}
    return await BridgeStore.enqueue(bridge_id, method="local_action", params=params)


async def test_only_a_desktops_local_access_bridge_opens(registry_db: None) -> None:
    for body in (
        BridgeOpenBody(root_path="/Users/test/project", hostname="laptop.local"),
        BridgeOpenBody(client="desktop", root_path="/Users/test/project", hostname="laptop.local"),
    ):
        with pytest.raises(HTTPException) as refused:
            await api_open_bridge(body, principal=OWNER)
        assert refused.value.status_code == 410


async def test_a_long_poll_returns_a_request_that_arrives_while_it_waits(
    registry_db: None,
) -> None:
    bridge_id = await _open()
    poll = asyncio.create_task(
        api_claim_bridge_requests(bridge_id, BridgeClaimBody(wait=10, limit=8), principal=OWNER)
    )
    await asyncio.sleep(0.05)
    request_id = await _enqueue(bridge_id, "~/hi.txt")

    answered = await asyncio.wait_for(poll, timeout=10)

    assert [request.request_id for request in answered.requests] == [request_id]
    assert answered.requests[0].params == {"operation": "read", "path": "~/hi.txt"}


async def test_a_long_poll_with_nothing_queued_returns_empty(registry_db: None) -> None:
    bridge_id = await _open()

    answered = await api_claim_bridge_requests(
        bridge_id, BridgeClaimBody(wait=0, limit=8), principal=OWNER
    )

    assert answered.requests == []


async def test_a_request_whose_poll_response_was_lost_is_offered_again(
    registry_db: None,
) -> None:
    bridge_id = await _open()
    request_id = await _enqueue(bridge_id)
    lost = await api_claim_bridge_requests(
        bridge_id, BridgeClaimBody(wait=0, limit=8), principal=OWNER
    )

    while_running = await api_claim_bridge_requests(
        bridge_id, BridgeClaimBody(wait=0, limit=8, held=[request_id]), principal=OWNER
    )
    after_losing_it = await api_claim_bridge_requests(
        bridge_id, BridgeClaimBody(wait=0, limit=8), principal=OWNER
    )

    assert [request.request_id for request in lost.requests] == [request_id]
    assert while_running.requests == []
    assert [request.request_id for request in after_losing_it.requests] == [request_id]


async def test_a_request_may_only_be_answered_once(registry_db: None) -> None:
    bridge_id = await _open()
    request_id = await _enqueue(bridge_id)
    await api_claim_bridge_requests(bridge_id, BridgeClaimBody(wait=0, limit=8), principal=OWNER)
    reply = BridgeReplyBody(result={"output": "", "exit_code": 0, "truncated": False})

    first = await api_answer_bridge_request(bridge_id, request_id, reply, principal=OWNER)
    with pytest.raises(HTTPException) as again:
        await api_answer_bridge_request(bridge_id, request_id, reply, principal=OWNER)

    assert first.status_code == 204
    assert again.value.status_code == 409


async def test_a_reply_must_carry_a_result_or_an_error() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        BridgeReplyBody()
    with pytest.raises(ValueError, match="exactly one"):
        BridgeReplyBody(result={"output": ""}, error="both")


async def test_another_user_cannot_see_the_bridge_at_all(registry_db: None) -> None:
    bridge_id = await _open()

    for call in (
        api_bridge_heartbeat(bridge_id, principal=INTRUDER),
        api_claim_bridge_requests(bridge_id, BridgeClaimBody(wait=0, limit=8), principal=INTRUDER),
        api_close_bridge(bridge_id, principal=INTRUDER),
    ):
        with pytest.raises(HTTPException) as refused:
            await call
        assert refused.value.status_code == 404


async def test_closing_a_bridge_stops_its_heartbeat_being_accepted(registry_db: None) -> None:
    bridge_id = await _open()

    closed = await api_close_bridge(bridge_id, principal=OWNER)
    with pytest.raises(HTTPException) as refused:
        await api_bridge_heartbeat(bridge_id, principal=OWNER)

    assert closed.status_code == 204
    assert refused.value.status_code == 409

    reopened = await api_open_bridge(_body(bridge_id), principal=OWNER)
    assert reopened.bridge_id == bridge_id
    assert (await api_bridge_heartbeat(bridge_id, principal=OWNER)).status_code == 204


async def test_reopening_a_bridge_nobody_owns_is_not_found(registry_db: None) -> None:
    with pytest.raises(HTTPException) as refused:
        await api_open_bridge(_body("nope"), principal=OWNER)

    assert refused.value.status_code == 404
