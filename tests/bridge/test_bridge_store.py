"""The guarantees the bridge queue makes to both halves of a sandbox bridge.

These run against a real migrated schema: the claim's ``SKIP LOCKED``, the
conditional finish and the heartbeat window are all SQL, so an in-memory double
would not be testing the thing that has to hold.
"""

import asyncio

import pytest
from sqlalchemy import text

from agent.bridge.protocol import JsonObject
from agent.bridge.store import Bridge, BridgeInUseError, BridgeStore
from agent.database import postgres

OWNER = "test-user"
OTHER = "someone-else"


async def _open(owner: str = OWNER) -> Bridge:
    bridge = await BridgeStore.register(
        owner_id=owner,
        hostname="laptop.local",
        root_path="/Users/test/project",
        label=None,
        bridge_id=None,
    )
    assert bridge is not None
    return bridge


async def _enqueue(bridge_id: str, command: str = "true") -> str:
    params: JsonObject = {"command": command, "timeout": None}
    return await BridgeStore.enqueue(bridge_id, method="execute", params=params)


async def _go_quiet(bridge_id: str, seconds: int = 600) -> None:
    async with postgres.transaction() as conn:
        await conn.execute(
            text(
                """
                UPDATE sandbox_bridge
                SET last_heartbeat_at = clock_timestamp() - make_interval(secs => :seconds)
                WHERE bridge_id = :bridge_id
                """
            ),
            {"bridge_id": bridge_id, "seconds": seconds},
        )


async def test_a_claimed_request_is_not_offered_to_another_claimer(registry_db: None) -> None:
    bridge = await _open()
    request_id = await _enqueue(bridge.bridge_id)

    async with postgres.transaction() as holding:
        held = await holding.execute(
            text(
                """
                SELECT request_id FROM sandbox_bridge_request
                WHERE bridge_id = :bridge_id AND status = 'pending'
                FOR UPDATE SKIP LOCKED
                """
            ),
            {"bridge_id": bridge.bridge_id},
        )
        assert [row.request_id for row in held] == [request_id]
        assert await BridgeStore.claim(bridge.bridge_id, limit=8) == []

    claimed = await BridgeStore.claim(bridge.bridge_id, limit=8)
    assert [request.request_id for request in claimed] == [request_id]
    assert await BridgeStore.claim(bridge.bridge_id, limit=8) == []


async def test_concurrent_claimers_split_one_request(registry_db: None) -> None:
    bridge = await _open()
    await _enqueue(bridge.bridge_id)

    first, second = await asyncio.gather(
        BridgeStore.claim(bridge.bridge_id, limit=8),
        BridgeStore.claim(bridge.bridge_id, limit=8),
    )

    assert len(first) + len(second) == 1


async def test_reopening_requeues_what_the_last_connection_never_answered(
    registry_db: None,
) -> None:
    bridge = await _open()
    request_id = await _enqueue(bridge.bridge_id)
    assert [
        request.request_id for request in await BridgeStore.claim(bridge.bridge_id, limit=8)
    ] == [request_id]
    await _go_quiet(bridge.bridge_id)

    reopened = await BridgeStore.register(
        owner_id=OWNER,
        hostname="laptop.local",
        root_path="/Users/test/other-project",
        label="after restart",
        bridge_id=bridge.bridge_id,
    )

    assert reopened is not None
    assert reopened.root_path == "/Users/test/other-project"
    assert [
        request.request_id for request in await BridgeStore.claim(bridge.bridge_id, limit=8)
    ] == [request_id]


async def test_a_bridge_another_process_is_serving_is_not_shared(registry_db: None) -> None:
    bridge = await _open()

    with pytest.raises(BridgeInUseError):
        await BridgeStore.register(
            owner_id=OWNER,
            hostname="laptop.local",
            root_path="/Users/test/project",
            label=None,
            bridge_id=bridge.bridge_id,
        )


async def test_reopening_someone_elses_bridge_finds_nothing(registry_db: None) -> None:
    bridge = await _open(OTHER)

    reopened = await BridgeStore.register(
        owner_id=OWNER,
        hostname="laptop.local",
        root_path="/Users/test/project",
        label=None,
        bridge_id=bridge.bridge_id,
    )

    assert reopened is None
    assert await BridgeStore.load(bridge.bridge_id, owner_id=OWNER) is None
    assert not await BridgeStore.heartbeat(bridge.bridge_id, owner_id=OWNER)


async def test_closing_fails_every_unanswered_request(registry_db: None) -> None:
    bridge = await _open()
    pending_id = await _enqueue(bridge.bridge_id, "pending")
    claimed_id = await _enqueue(bridge.bridge_id, "claimed")
    await BridgeStore.claim(bridge.bridge_id, limit=1)

    assert await BridgeStore.close(bridge.bridge_id, owner_id=OWNER)

    for request_id in (pending_id, claimed_id):
        outcome = await BridgeStore.outcome(bridge.bridge_id, request_id)
        assert outcome is not None
        assert outcome.status == "failed"
        assert outcome.error == "bridge closed"
    assert not await BridgeStore.heartbeat(bridge.bridge_id, owner_id=OWNER)
    assert await BridgeStore.close(bridge.bridge_id, owner_id=OWNER)


async def test_a_request_is_answered_at_most_once(registry_db: None) -> None:
    bridge = await _open()
    request_id = await _enqueue(bridge.bridge_id)
    await BridgeStore.claim(bridge.bridge_id, limit=8)

    assert (
        await BridgeStore.complete(bridge.bridge_id, request_id, result={"output": "first"})
        == "completed"
    )
    assert (
        await BridgeStore.complete(bridge.bridge_id, request_id, result={"output": "second"})
        == "already_finished"
    )
    assert await BridgeStore.complete(bridge.bridge_id, "nope", result={}) == "missing"

    outcome = await BridgeStore.outcome(bridge.bridge_id, request_id)
    assert outcome is not None
    assert outcome.result == {"output": "first"}


async def test_pruning_closes_a_bridge_that_stopped_heartbeating(registry_db: None) -> None:
    quiet = await _open()
    request_id = await _enqueue(quiet.bridge_id)
    live = await _open()
    await _go_quiet(quiet.bridge_id)

    assert await BridgeStore.prune_stale() == 1

    outcome = await BridgeStore.outcome(quiet.bridge_id, request_id)
    assert outcome is not None
    assert outcome.status == "failed"
    assert outcome.error == "bridge disconnected"
    reloaded = await BridgeStore.load(live.bridge_id, owner_id=OWNER)
    assert reloaded is not None
    assert reloaded.is_alive
