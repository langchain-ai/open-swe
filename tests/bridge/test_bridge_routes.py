"""The wire contract the CLI is built against, called the way FastAPI calls it."""

import asyncio

import pytest
from fastapi import HTTPException

from agent.bridge.protocol import JsonObject
from agent.bridge.routes import (
    BridgeOpenBody,
    BridgeReplyBody,
    api_answer_bridge_request,
    api_bridge_heartbeat,
    api_claim_bridge_requests,
    api_close_bridge,
    api_open_bridge,
)
from agent.bridge.store import BridgeStore
from agent.threads.principals import Principal

OWNER = Principal.of_login("test-user")
INTRUDER = Principal.of_login("someone-else")


async def _open() -> str:
    opened = await api_open_bridge(
        BridgeOpenBody(root_path="/Users/test/project", hostname="laptop.local"),
        principal=OWNER,
    )
    return opened.bridge_id


async def _enqueue(bridge_id: str, command: str = "true") -> str:
    params: JsonObject = {"command": command, "timeout": None}
    return await BridgeStore.enqueue(bridge_id, method="execute", params=params)


async def test_a_long_poll_returns_a_request_that_arrives_while_it_waits(
    registry_db: None,
) -> None:
    bridge_id = await _open()
    poll = asyncio.create_task(
        api_claim_bridge_requests(bridge_id, wait=10, limit=8, principal=OWNER)
    )
    await asyncio.sleep(0.05)
    request_id = await _enqueue(bridge_id, "echo hi")

    answered = await asyncio.wait_for(poll, timeout=10)

    assert [request.request_id for request in answered.requests] == [request_id]
    assert answered.requests[0].params == {"command": "echo hi", "timeout": None}


async def test_a_long_poll_with_nothing_queued_returns_empty(registry_db: None) -> None:
    bridge_id = await _open()

    answered = await api_claim_bridge_requests(bridge_id, wait=0, limit=8, principal=OWNER)

    assert answered.requests == []


async def test_a_request_may_only_be_answered_once(registry_db: None) -> None:
    bridge_id = await _open()
    request_id = await _enqueue(bridge_id)
    await api_claim_bridge_requests(bridge_id, wait=0, limit=8, principal=OWNER)
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
        api_claim_bridge_requests(bridge_id, wait=0, limit=8, principal=INTRUDER),
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

    reopened = await api_open_bridge(
        BridgeOpenBody(
            root_path="/Users/test/project", hostname="laptop.local", bridge_id=bridge_id
        ),
        principal=OWNER,
    )
    assert reopened.bridge_id == bridge_id
    assert (await api_bridge_heartbeat(bridge_id, principal=OWNER)).status_code == 204


async def test_reopening_a_bridge_nobody_owns_is_not_found(registry_db: None) -> None:
    with pytest.raises(HTTPException) as refused:
        await api_open_bridge(
            BridgeOpenBody(
                root_path="/Users/test/project", hostname="laptop.local", bridge_id="nope"
            ),
            principal=OWNER,
        )

    assert refused.value.status_code == 404
