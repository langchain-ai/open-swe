import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from openswe.database.postgres import transaction
from openswe.jobs import queue
from openswe.jobs.queue import Hold, Job
from openswe.jobs.worker import JobWorker

pytestmark = pytest.mark.usefixtures("registry_db")

_PAST = datetime(2026, 1, 1, tzinfo=UTC)


async def _due_now(job: Job) -> None:
    async with transaction() as conn:
        await conn.execute(
            text("UPDATE job SET due_at = :at WHERE id = :id"), {"at": _PAST, "id": job.id}
        )


def _worker(ran: list[Job], *, fail: bool = False) -> JobWorker:
    worker = JobWorker()

    @worker.register("probe")
    async def probe(job: Job) -> None:
        ran.append(job)
        await asyncio.sleep(0.05)
        if fail:
            raise RuntimeError("probe failed")

    return worker


async def test_a_due_job_runs_once_however_many_workers_poll() -> None:
    ran: list[Job] = []
    await queue.schedule("probe", {"n": 1}, due_at=_PAST)

    counts = await asyncio.gather(*(_worker(ran).run_due() for _ in range(4)))

    assert sum(counts) == 1
    assert [job.payload for job in ran] == [{"n": 1}]
    assert await queue.find({}) == []


async def test_a_reclaimed_wakeup_does_not_launch_a_second_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.jobs import kinds

    launched: list[str] = []

    async def wakeup(payload: dict[str, object], *, job_id: str, due_at: datetime) -> str:
        launched.append(job_id)
        return "run-1"

    monkeypatch.setattr(kinds, "run_thread_wakeup", wakeup)
    job = await queue.schedule("thread_wakeup", {"thread_id": "t1"}, due_at=_PAST)
    [claimed] = await queue.claim_due(1)
    await kinds._wake_thread(claimed)
    async with transaction() as conn:
        await conn.execute(
            text("UPDATE job SET locked_until = :at WHERE id = :id"),
            {"at": _PAST, "id": job.id},
        )

    assert await kinds.WORKER.run_due() == 1

    assert launched == [str(job.id)]
    assert await queue.find({}) == []


async def test_a_cron_job_moves_to_its_next_fire_time_in_its_timezone() -> None:
    job = await queue.schedule("probe", cron="0 9 * * *", timezone="America/New_York")
    await _due_now(job)

    assert await _worker([]).run_due() == 1

    [after] = await queue.find({})
    assert after.due_at > datetime.now(UTC)
    assert after.due_at.astimezone(job.next_fire_after(_PAST).tzinfo).hour in {13, 14}


async def test_a_failing_one_shot_backs_off_then_gives_up() -> None:
    job = await queue.schedule("probe", due_at=_PAST)
    ran: list[Job] = []
    for attempt in range(1, queue.MAX_ATTEMPTS + 1):
        await _due_now(job)
        assert await _worker(ran, fail=True).run_due() == 1
        remaining = await queue.find({})
        if attempt < queue.MAX_ATTEMPTS:
            [retried] = remaining
            assert retried.attempts == attempt
            assert retried.due_at > datetime.now(UTC)
        else:
            assert remaining == []
    assert len(ran) == queue.MAX_ATTEMPTS


async def test_a_key_replaces_the_job_queued_under_it() -> None:
    await queue.schedule("probe", {"v": 1}, due_at=_PAST, key="probe:a")
    await queue.schedule("probe", {"v": 2}, cron="*/5 * * * *", key="probe:a")

    [job] = await queue.find({})
    assert (job.payload, job.cron) == ({"v": 2}, "*/5 * * * *")


async def test_a_job_replaced_while_it_runs_keeps_its_replacement() -> None:
    worker = JobWorker()

    @worker.register("probe")
    async def replace_itself(job: Job) -> None:
        await queue.schedule(
            "probe", {"v": "new"}, due_at=datetime.now(UTC) + timedelta(hours=1), key="probe:b"
        )

    await queue.schedule("probe", {"v": "old"}, due_at=_PAST, key="probe:b")
    await worker.run_due()

    [job] = await queue.find({})
    assert job.payload == {"v": "new"}


async def test_a_run_that_ends_after_its_job_was_replaced_and_reclaimed_leaves_it_alone() -> None:
    await queue.schedule("probe", {"v": "old"}, due_at=_PAST, key="probe:c")
    [first] = await queue.claim_due(1)
    await queue.schedule("probe", {"v": "new"}, due_at=_PAST, key="probe:c")
    [second] = await queue.claim_due(1)

    await first.finish()

    assert await first.hold() == Hold.REPLACED
    assert await second.hold() == Hold.HELD
    [job] = await queue.find({})
    assert job.payload == {"v": "new"}


async def test_jobs_are_found_and_cancelled_by_metadata() -> None:
    await queue.schedule("probe", metadata={"thread_id": "t1", "kind": "wake"}, due_at=_PAST)
    await queue.schedule("probe", metadata={"thread_id": "t2", "kind": "wake"}, due_at=_PAST)

    assert [job.metadata["thread_id"] for job in await queue.find({"thread_id": "t1"})] == ["t1"]
    assert await queue.cancel_matching({"thread_id": "t1"}) == 1
    assert [job.metadata["thread_id"] for job in await queue.find({"kind": "wake"})] == ["t2"]


async def test_cancelling_a_running_job_stops_its_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    from openswe.jobs import worker as worker_module

    monkeypatch.setattr(worker_module, "_HEARTBEAT_SECONDS", 0.05)
    started = asyncio.Event()
    finished: list[bool] = []
    worker = JobWorker()

    @worker.register("probe")
    async def long_running(job: Job) -> None:
        started.set()
        await asyncio.sleep(5)
        finished.append(True)

    job = await queue.schedule("probe", due_at=_PAST)
    running = asyncio.create_task(worker.run_due())
    await started.wait()
    await queue.cancel(job.id)

    async with asyncio.timeout(2):
        await running
    assert finished == []


@pytest.mark.usefixtures("memory_event_claims")
async def test_an_automation_fire_claimed_again_after_a_crash_launches_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.jobs import kinds

    launched: list[str] = []

    async def launch(schedule_id: str, trigger_id: str | None) -> dict[str, str]:
        launched.append(schedule_id)
        return {"status": "started"}

    monkeypatch.setattr(kinds, "launch_scheduled_agent_run", launch)
    job = await queue.schedule("agent_schedule", {"schedule_id": "a1"}, cron="0 9 * * *")

    # The first run launched, then its worker stopped before finishing the job.
    await kinds._agent_schedule(job)
    await kinds._agent_schedule(job)
    next_fire = job.model_copy(update={"due_at": job.next_fire_after(job.due_at)})
    await kinds._agent_schedule(next_fire)

    assert launched == ["a1", "a1"]
