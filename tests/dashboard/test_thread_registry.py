from datetime import timedelta

import pytest
from fastapi import HTTPException

from agent.dashboard import thread_api
from agent.dashboard.thread_registry import (
    MAX_MESSAGE_PAYLOAD_BYTES,
    PostgresRegistry,
    SqliteRegistry,
    ThreadCreate,
    _build_registry,
    set_thread_registry_for_testing,
    utcnow,
)


@pytest.fixture
async def registry(tmp_path):
    value = SqliteRegistry(tmp_path / "threads.sqlite3")
    await value.initialize()
    set_thread_registry_for_testing(value)
    try:
        yield value
    finally:
        set_thread_registry_for_testing(None)
        await value.close()


async def test_list_is_owner_scoped_filtered_and_keyset_paginated(registry) -> None:
    now = utcnow()
    for index in range(3):
        await registry.create(
            ThreadCreate(
                id=f"owner-{index}",
                owner_login="owner",
                title=f"Needle {index}",
                environment="local" if index == 2 else "cloud",
                device_id="device-1" if index == 2 else None,
                created_at=now + timedelta(seconds=index),
                updated_at=now + timedelta(seconds=index),
            )
        )
    await registry.create(ThreadCreate(id="other", owner_login="other", title="Needle"))

    first = await registry.list("owner", q="needle", limit=2)
    second = await registry.list("owner", q="needle", limit=2, cursor=first.cursor)
    local = await registry.list("owner", environment="local")

    assert [row.id for row in first.items] == ["owner-2", "owner-1"]
    assert first.has_more is True
    assert [row.id for row in second.items] == ["owner-0"]
    assert [row.id for row in local.items] == ["owner-2"]


async def test_late_transition_cannot_clobber_superseding_run(registry) -> None:
    await registry.create(ThreadCreate(id="thread", owner_login="owner"))
    await registry.transition("thread", "run-1", "queued", environment="cloud")
    await registry.transition("thread", "run-1", "running", environment="cloud")
    await registry.transition(
        "thread", "run-2", "queued", environment="cloud", guard_run_id="run-1"
    )

    delayed = await registry.transition("thread", "run-1", "queued", environment="cloud")
    unchanged = await registry.transition("thread", "run-1", "finished", environment="cloud")
    finished = await registry.transition("thread", "run-2", "finished", environment="cloud")

    assert (delayed.status, delayed.status_run_id) == ("queued", "run-2")
    assert (unchanged.status, unchanged.status_run_id) == ("queued", "run-2")
    assert (finished.status, finished.status_run_id) == ("finished", "run-2")


async def test_messages_are_deduplicated_bounded_and_incremental(registry) -> None:
    await registry.create(ThreadCreate(id="thread", owner_login="owner"))
    message = {"id": "message-1", "author": "agent", "chunks": [{"kind": "text", "text": "ok"}]}
    assert await registry.append_messages("thread", "run", [message, message]) == 1
    assert (
        await registry.append_messages(
            "thread",
            "run",
            [
                {
                    "id": "message-2",
                    "author": "tool",
                    "chunks": [{"kind": "text", "text": "x" * (MAX_MESSAGE_PAYLOAD_BYTES + 1)}],
                }
            ],
        )
        == 2
    )

    messages = await registry.get_messages("thread", after_seq=1)

    assert len(messages) == 1
    assert messages[0]["seq"] == 2
    assert messages[0]["payload"]["isTruncated"] is True


async def test_events_are_replayable_owner_scoped_and_prunable(registry) -> None:
    await registry.create(ThreadCreate(id="mine", owner_login="owner"))
    await registry.create(ThreadCreate(id="theirs", owner_login="other"))
    await registry.update_meta("mine", title="Renamed")

    owner_events = await registry.events_since(0, "owner")
    replay = await registry.events_since(owner_events[0].id, "owner")

    assert [event.kind for event in owner_events] == ["thread.created", "thread.meta"]
    assert [event.kind for event in replay] == ["thread.meta"]
    assert await registry.prune_events(older_than=utcnow() + timedelta(seconds=1)) == 3
    assert await registry.events_since(0, None) == []


async def test_device_heartbeat_is_scoped_to_owner(registry) -> None:
    await registry.record_heartbeat("device", "owner", "Laptop")

    assert (await registry.device("device", "owner"))["name"] == "Laptop"
    assert await registry.device("device", "other") is None


async def test_thread_detail_uses_registry_status_and_owner(registry) -> None:
    await registry.create(ThreadCreate(id="thread", owner_login="owner"))
    await registry.transition("thread", "run", "queued", environment="cloud")
    await registry.transition("thread", "run", "finished", environment="cloud")

    unread = await thread_api.get_dashboard_thread("thread", "owner", mark_viewed=False)
    viewed = await thread_api.get_dashboard_thread("thread", "owner")

    assert unread["status"] == "finished"
    assert unread["viewed"] is False
    assert viewed["viewed"] is True
    with pytest.raises(HTTPException) as exc_info:
        await thread_api.get_dashboard_thread("thread", "other")
    assert exc_info.value.status_code == 404


def test_registry_configuration_fails_closed_outside_local_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "DATABASE_URL",
        "POSTGRES_URI",
        "OPEN_SWE_LOCAL_ONLY",
        "OPEN_SWE_REGISTRY_SQLITE_PATH",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="thread registry requires"):
        _build_registry()

    monkeypatch.setenv("DATABASE_URL", "postgresql://registry.invalid/open_swe")
    assert isinstance(_build_registry(), PostgresRegistry)
    monkeypatch.setenv("OPEN_SWE_LOCAL_ONLY", "1")
    assert isinstance(_build_registry(), SqliteRegistry)
