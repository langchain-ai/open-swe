"""The guarantees the bridge queue makes to both halves of a sandbox bridge.

These run against a real migrated schema: the claim's ``SKIP LOCKED``, the
conditional finish and the heartbeat window are all SQL, so an in-memory double
would not be testing the thing that has to hold.
"""

import asyncio
from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from openswe.bridge.local_access_policy import LocalAccess
from openswe.bridge.protocol import JsonObject
from openswe.bridge.store import Bridge, BridgeInUseError, BridgeStore, SandboxBridgeBinding
from openswe.dashboard.workspace_settings import WorkspaceSettingsUpdate, upsert_instance_settings
from openswe.database import postgres
from tests.conftest import LangSmithUsers

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


async def test_local_access_requires_an_enabled_private_owner_cloud_run(
    registry_db: None, langsmith_users: LangSmithUsers
) -> None:
    langsmith_users.add(OWNER)
    langsmith_users.add(OTHER)
    bridge = await BridgeStore.register(
        owner_id=OWNER,
        client="desktop",
        hostname="laptop.local",
        root_path="local-access://approved-actions",
        label=None,
        bridge_id=None,
    )
    assert bridge is not None
    metadata: dict[str, object] = {"owner_login": OWNER, "visibility": "private"}
    with pytest.raises(HTTPException, match="disabled"):
        await LocalAccess.offer(bridge.bridge_id, metadata=metadata, login=OWNER, owner_id=OWNER)
    await upsert_instance_settings(WorkspaceSettingsUpdate(cloud_first_desktop_enabled=True))
    assert (
        await LocalAccess.offer(bridge.bridge_id, metadata=metadata, login=OWNER, owner_id=OWNER)
        == bridge.bridge_id
    )
    for changed, login in [
        ({**metadata, "visibility": "workspace"}, OWNER),
        (metadata, OTHER),
        ({**metadata, "sandbox_id": bridge.sandbox_id}, OWNER),
    ]:
        with pytest.raises(HTTPException):
            await LocalAccess.offer(bridge.bridge_id, metadata=changed, login=login, owner_id=login)
    with pytest.raises(HTTPException):
        SandboxBridgeBinding.of(bridge)
    legacy = await _open()
    with pytest.raises(HTTPException, match="approved local actions"):
        await LocalAccess.offer(legacy.bridge_id, metadata=metadata, login=OWNER, owner_id=OWNER)
    await LocalAccess.allow_binding(legacy, metadata)
    desktop = legacy.model_copy(update={"client": "desktop"})
    with pytest.raises(HTTPException, match="Cloud-first"):
        await LocalAccess.allow_binding(desktop, metadata)
    await upsert_instance_settings(WorkspaceSettingsUpdate(cloud_first_desktop_enabled=False))
    await LocalAccess.allow_binding(desktop, metadata)
    with pytest.raises(HTTPException, match="disabled"):
        await LocalAccess.offer(bridge.bridge_id, metadata=metadata, login=OWNER, owner_id=OWNER)


async def test_a_run_drops_local_access_it_cannot_have_rather_than_failing(
    registry_db: None, langsmith_users: LangSmithUsers
) -> None:
    langsmith_users.add(OWNER)
    bridge = await BridgeStore.register(
        owner_id=OWNER,
        client="desktop",
        hostname="laptop.local",
        root_path="local-access://approved-actions",
        label=None,
        bridge_id=None,
    )
    assert bridge is not None
    metadata: dict[str, object] = {"owner_login": OWNER, "visibility": "private"}
    # The desktop's cached flag says on; the instance says off.
    assert (
        await LocalAccess.offer_for_run(
            bridge.bridge_id, metadata=metadata, login=OWNER, owner_id=OWNER
        )
        is None
    )
    with pytest.raises(HTTPException) as malformed:
        await LocalAccess.offer_for_run("  ", metadata=metadata, login=OWNER, owner_id=OWNER)
    assert malformed.value.status_code == 422


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


async def _request_partitions() -> set[str]:
    async with postgres.transaction() as conn:
        rows = await conn.execute(
            text(
                "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "WHERE i.inhparent = 'sandbox_bridge_request'::regclass"
            )
        )
        return set(rows.scalars().all())


async def test_rotation_drops_request_days_no_waiter_can_still_be_reading(
    registry_db: None,
) -> None:
    # Past the days the migration partitioned, so those are dropped too.
    await BridgeStore.rotate_partitions(date(2099, 9, 1))
    assert await _request_partitions() == {
        "sandbox_bridge_request_20990901",
        "sandbox_bridge_request_20990902",
    }

    await BridgeStore.rotate_partitions(date(2099, 9, 3))
    assert await _request_partitions() == {
        "sandbox_bridge_request_20990902",
        "sandbox_bridge_request_20990903",
        "sandbox_bridge_request_20990904",
    }


async def test_a_persons_experiment_opts_them_into_local_access_without_the_workspace(
    registry_db: None, langsmith_users: LangSmithUsers
) -> None:
    owner = langsmith_users.add(OWNER)
    bridge = await BridgeStore.register(
        owner_id=OWNER,
        client="desktop",
        hostname="laptop.local",
        root_path="local-access://approved-actions",
        label=None,
        bridge_id=None,
    )
    assert bridge is not None
    metadata: dict[str, object] = {"owner_login": OWNER, "visibility": "private"}
    with pytest.raises(HTTPException, match="disabled"):
        await LocalAccess.offer(bridge.bridge_id, metadata=metadata, login=OWNER, owner_id=OWNER)
    owner.preferences = {"experimental_cloud_first_desktop": True}
    assert (
        await LocalAccess.offer(bridge.bridge_id, metadata=metadata, login=OWNER, owner_id=OWNER)
        == bridge.bridge_id
    )
    # The opt-in is the owner's own: it never reaches someone else's thread.
    with pytest.raises(HTTPException):
        await LocalAccess.offer(
            bridge.bridge_id,
            metadata={**metadata, "visibility": "workspace"},
            login=OWNER,
            owner_id=OWNER,
        )
