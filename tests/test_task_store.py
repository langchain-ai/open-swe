import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from agent.database import postgres
from agent.tasks import store


async def test_legacy_reads_and_writes_without_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(postgres, "configured", lambda: False)
    assert await store.task_for_thread("legacy") is None
    assert await store.membership_for_thread("legacy") is None
    async with store.thread_lock("legacy"):
        with pytest.raises(RuntimeError, match="POSTGRES_URI"):
            await store.ensure_task(
                "legacy",
                workspace="default",
                title="Fix login",
                acceptance_criteria=["Login works"],
            )


async def test_membership_depth_and_completion_are_coordinator_owned(registry_db: None) -> None:
    task = await store.ensure_task(
        "coordinator", workspace="default", title="Fix login", acceptance_criteria=["Login works"]
    )
    repeated = await store.ensure_task(
        "coordinator", workspace="other", title="Ignored", acceptance_criteria=["Ignored"]
    )
    assert repeated == task
    async with asyncio.timeout(5), store.thread_lock("coordinator"):
        await store.create_delegation(
            "coordinator",
            worker_thread_id="worker",
            instructions="Fix login",
            model=None,
            effort=None,
        )
    assert (await store.membership_for_thread("worker")) == store.Membership(
        task_id=task.id, thread_id="worker", role="worker"
    )
    with pytest.raises(PermissionError):
        await store.create_delegation(
            "worker", worker_thread_id="grandchild", instructions="Help", model=None, effort=None
        )
    with pytest.raises(PermissionError):
        await store.update_task("worker", title="Different", acceptance_criteria=["Different"])
    with pytest.raises(PermissionError):
        await store.complete_task("worker", evidence=["Works"])
    with pytest.raises(ValueError, match="pending or running"):
        await store.complete_task("coordinator", evidence=["Works"])
    await store.finish_delegation("worker", status="completed")
    current = await store.task_for_thread("coordinator")
    assert current is not None and current.delegated and current.status == "active"
    with pytest.raises(ValueError, match="every acceptance criterion"):
        await store.complete_task("coordinator", evidence=[""])
    completed = await store.complete_task("coordinator", evidence=["Login regression test passes"])
    assert completed.status == "completed"
    assert completed.completion_evidence == ["Login regression test passes"]
    with pytest.raises(ValueError, match="active tasks"):
        await store.create_delegation(
            "coordinator", worker_thread_id="later", instructions="Help", model=None, effort=None
        )


async def test_database_rejects_moving_members_and_resetting_delegation(registry_db: None) -> None:
    task = await store.ensure_task(
        "coordinator", workspace="default", title="Fix login", acceptance_criteria=["Login works"]
    )
    await store.create_delegation(
        "coordinator", worker_thread_id="worker", instructions="Fix login", model=None, effort=None
    )
    with pytest.raises(DBAPIError, match="permanent"):
        async with postgres.transaction() as conn:
            await conn.execute(
                text("UPDATE agent_task SET delegated = false WHERE id = :id"), {"id": task.id}
            )
    with pytest.raises(DBAPIError, match="permanent"):
        async with postgres.transaction() as conn:
            await conn.execute(
                text("UPDATE task_membership SET role = 'coordinator' WHERE thread_id = 'worker'")
            )
    with pytest.raises(IntegrityError):
        async with postgres.transaction() as conn:
            await conn.execute(
                text("""
                    INSERT INTO task_delegation
                        (id, task_id, coordinator_thread_id, worker_thread_id, instructions)
                    VALUES (:id, :task_id, 'worker', 'worker', 'Nested delegation')
                """),
                {"id": str(uuid4()), "task_id": task.id},
            )


async def test_parallel_worker_claims_cannot_put_one_thread_in_two_tasks(registry_db: None) -> None:
    for coordinator in ("first", "second"):
        await store.ensure_task(
            coordinator, workspace="default", title="Fix login", acceptance_criteria=["Login works"]
        )
    results = await asyncio.gather(
        *(
            store.create_delegation(
                coordinator,
                worker_thread_id="shared-worker",
                instructions="Fix login",
                model=None,
                effort=None,
            )
            for coordinator in ("first", "second")
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, store.Delegation) for result in results) == 1
    assert sum(isinstance(result, (ValueError, IntegrityError)) for result in results) == 1
    records = [await store.task_for_thread(coordinator) for coordinator in ("first", "second")]
    assert sum(record is not None and record.delegated for record in records) == 1


async def test_lock_is_reentrant_but_not_inherited_by_parallel_effects(registry_db: None) -> None:
    attempted = asyncio.Event()
    entered = asyncio.Event()

    async def parallel_effect() -> None:
        attempted.set()
        async with store.thread_lock("before-task-exists"):
            entered.set()

    async with asyncio.timeout(5):
        async with store.thread_lock("before-task-exists"):
            async with store.thread_lock("before-task-exists"):
                child = asyncio.create_task(parallel_effect())
                await attempted.wait()
                assert not entered.is_set()
        await child
        assert entered.is_set()


async def test_first_delegation_waits_for_admitted_implementation(registry_db: None) -> None:
    await store.ensure_task(
        "coordinator", workspace="default", title="Fix login", acceptance_criteria=["Login works"]
    )
    attempted = asyncio.Event()

    async def delegate() -> store.Delegation:
        attempted.set()
        return await store.create_delegation(
            "coordinator",
            worker_thread_id="worker",
            instructions="Fix login",
            model=None,
            effort=None,
        )

    async with asyncio.timeout(5):
        async with store.thread_lock("coordinator"):
            child = asyncio.create_task(delegate())
            await attempted.wait()
            assert not child.done()
            current = await store.task_for_thread("coordinator")
            assert current is not None and not current.delegated
        await child
        current = await store.task_for_thread("coordinator")
        assert current is not None and current.delegated


async def test_worker_completion_races_and_events_do_not_reopen_or_duplicate(
    registry_db: None,
) -> None:
    await store.ensure_task(
        "coordinator", workspace="default", title="Fix login", acceptance_criteria=["Login works"]
    )
    await store.create_delegation(
        "coordinator", worker_thread_id="worker", instructions="Fix login", model=None, effort=None
    )
    await store.finish_delegation("worker", status="completed", run_id="fast-run")
    await store.set_delegation_run("worker", "fast-run")
    finished = await store.delegation_for_worker("worker")
    assert finished is not None and finished.status == "completed"
    await store.set_delegation_run("worker", "next-run")
    await store.finish_delegation("worker", status="failed", run_id="fast-run")
    running = await store.delegation_for_worker("worker")
    assert running is not None and running.status == "running" and running.run_id == "next-run"
    events = await asyncio.gather(
        *(
            store.record_event(
                "worker", event_key="progress-1", kind="progress", content="Tests pass"
            )
            for _ in range(2)
        )
    )
    assert events[0] == events[1]
    async with store.event_delivery_lock("coordinator"):
        assert await store.pending_events("coordinator") == [events[0]]
        await store.mark_event_delivered(events[0].id)
    assert await store.pending_events("coordinator") == []
    repeated = await store.record_event(
        "worker",
        event_key="progress-1",
        kind="progress",
        content="Do not replace delivered content",
    )
    assert repeated.delivered and repeated.content == "Tests pass"


async def test_pending_follow_up_survives_worker_completion_and_blocks_task_completion(
    registry_db: None,
) -> None:
    await store.ensure_task(
        "coordinator", workspace="default", title="Fix login", acceptance_criteria=["Login works"]
    )
    await store.create_delegation(
        "coordinator", worker_thread_id="worker", instructions="Fix login", model=None, effort=None
    )
    await store.set_delegation_run("worker", "first-run")
    intent = await store.record_worker_dispatch(
        "worker", dispatch_key="follow-up", content="Test password reset too"
    )
    await store.finish_delegation("worker", status="completed", run_id="first-run")
    assert await store.pending_worker_dispatches() == [intent]
    with pytest.raises(ValueError, match="pending or running"):
        await store.complete_task("coordinator", evidence=["First result passed"])
    await store.register_worker_dispatch("worker", "follow-up", "second-run")
    await store.set_delegation_run("worker", "second-run")
    await store.finish_delegation("worker", status="completed", run_id="second-run")
    await store.settle_worker_dispatch("worker", "second-run")
    assert await store.pending_worker_dispatches() == []
    assert (
        await store.complete_task("coordinator", evidence=["Both results passed"])
    ).status == "completed"


async def test_lock_does_not_consume_the_connection_needed_by_its_body(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(
        postgres.engine().url, pool_size=1, max_overflow=0, pool_timeout=0.2
    )
    monkeypatch.setattr(postgres, "_ENGINE", engine)
    try:
        async with asyncio.timeout(5), store.thread_lock("coordinator"):
            task = await store.ensure_task(
                "coordinator",
                workspace="default",
                title="Fix login",
                acceptance_criteria=["Login works"],
            )
        assert task.coordinator_thread_id == "coordinator"
    finally:
        await engine.dispose()
