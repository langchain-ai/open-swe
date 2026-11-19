"""What a run gets back when it asks its owner's desktop for an approved local action.

The "desktop" here is a coroutine doing exactly what the real one does over HTTP:
claim the request, run it, post the answer back.
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterator

import pytest
from langchain_core.runnables.config import var_child_runnable_config

from openswe.bridge.connection import BridgeConnection
from openswe.bridge.protocol import JsonObject, LocalActionParams
from openswe.bridge.store import Bridge, BridgeStore, ClaimedRequest

OWNER = "github:alice"
READ = LocalActionParams(operation="read", path="~/notes.txt", timeout=5)

Reply = Callable[[ClaimedRequest], JsonObject]


@pytest.fixture(autouse=True)
def _owners_run() -> Iterator[None]:
    token = var_child_runnable_config.set({"configurable": {"github_login": "alice"}})
    yield
    var_child_runnable_config.reset(token)


async def _open(owner: str = OWNER) -> Bridge:
    bridge = await BridgeStore.register(
        owner_id=owner, hostname="laptop.local", label=None, bridge_id=None
    )
    assert bridge is not None
    return bridge


def _desktop(
    bridge_id: str, *, result: Reply | None = None, error: str | None = None
) -> Awaitable[ClaimedRequest]:
    """A stand-in desktop that answers the next request on ``bridge_id`` and stops."""

    async def run() -> ClaimedRequest:
        for _ in range(500):
            claimed = await BridgeStore.claim(bridge_id, limit=8)
            if claimed:
                request = claimed[0]
                await BridgeStore.complete(
                    bridge_id,
                    request.request_id,
                    result=None if result is None else result(request),
                    error=error,
                )
                return request
            await asyncio.sleep(0.01)
        raise AssertionError("no bridge request arrived")

    return asyncio.create_task(run())


async def test_a_local_action_round_trips_through_the_queue(registry_db: None) -> None:
    bridge = await _open()
    connection = await BridgeConnection.connect(bridge.bridge_id)
    desktop = _desktop(bridge.bridge_id, result=lambda _request: {"content": "hi\n"})

    assert await connection.local_action(READ) == {"content": "hi\n"}

    request = await desktop
    assert request.method == "local_action"
    assert request.params == READ.dump()


async def test_an_error_reply_surfaces_as_an_exception(registry_db: None) -> None:
    bridge = await _open()
    connection = await BridgeConnection.connect(bridge.bridge_id)
    desktop = _desktop(bridge.bridge_id, error="the person declined")

    with pytest.raises(RuntimeError, match="the person declined"):
        await connection.local_action(READ)

    await desktop


async def test_closing_the_bridge_ends_a_waiting_request(registry_db: None) -> None:
    bridge = await _open()
    connection = await BridgeConnection.connect(bridge.bridge_id)

    async def close_once_claimed() -> None:
        for _ in range(500):
            if await BridgeStore.claim(bridge.bridge_id, limit=8):
                await BridgeStore.close(bridge.bridge_id, owner_id=OWNER)
                return
            await asyncio.sleep(0.01)
        raise AssertionError("no bridge request arrived")

    closer = asyncio.create_task(close_once_claimed())
    with pytest.raises(RuntimeError, match="bridge closed"):
        await connection.local_action(READ)
    await closer


@pytest.mark.parametrize(
    ("owner", "owners_run", "others_run"),
    [
        (OWNER, {"github_login": "Alice"}, {"github_login": "mallory"}),
        ("system:api_key/1", {"started_by_id": "api_key:1"}, {"github_login": "alice"}),
        (OWNER, {"github_login": "alice"}, {}),
    ],
)
async def test_only_the_owners_runs_reach_the_desktop(
    registry_db: None, owner: str, owners_run: JsonObject, others_run: JsonObject
) -> None:
    bridge = await _open(owner)
    token = var_child_runnable_config.set({"configurable": owners_run})
    try:
        connection = await BridgeConnection.connect(bridge.bridge_id)
    finally:
        var_child_runnable_config.reset(token)

    token = var_child_runnable_config.set({"configurable": others_run})
    try:
        with pytest.raises(PermissionError):
            await BridgeConnection.connect(bridge.bridge_id)
        # A connection outlives the run that opened it.
        with pytest.raises(PermissionError):
            await connection.local_action(READ)
    finally:
        var_child_runnable_config.reset(token)
    assert await BridgeStore.claim(bridge.bridge_id, limit=8) == []
