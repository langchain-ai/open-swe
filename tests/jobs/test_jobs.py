import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from openswe.database.postgres import transaction
from openswe.jobs import queue
from openswe.jobs.queue import Hold, Job
from openswe.jobs.worker import JobWorker

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


@pytest.mark.usefixtures("registry_db")
async def test_a_due_job_runs_once_however_many_workers_poll() -> None:
    ran: list[Job] = []
    unrelated = await queue.schedule("other", due_at=_PAST - timedelta(seconds=1))
    await queue.schedule("probe", {"n": 1}, due_at=_PAST)

    counts = await asyncio.gather(*(_worker(ran).run_due(limit=1) for _ in range(4)))

    assert sum(counts) == 1
    assert [job.payload for job in ran] == [{"n": 1}]
    assert [job.id for job in await queue.find({})] == [unrelated.id]
    assert await queue.claim_due(1, kinds=["probe"]) == []
    [other] = await queue.claim_due(1)
    assert other.id == unrelated.id
    await other.finish()
    assert await queue.find({}) == []


@pytest.mark.usefixtures("registry_db")
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


@pytest.mark.usefixtures("registry_db")
async def test_a_cron_job_moves_to_its_next_fire_time_in_its_timezone() -> None:
    job = await queue.schedule("probe", cron="0 9 * * *", timezone="America/New_York")
    await _due_now(job)

    assert await _worker([]).run_due() == 1

    [after] = await queue.find({})
    assert after.due_at > datetime.now(UTC)
    assert after.due_at.astimezone(ZoneInfo(job.timezone)).hour == 9
    assert after.claim_token is None


@pytest.mark.parametrize(
    ("timezone", "cron", "start", "expected"),
    [
        ("America/New_York", "59/15 * * * *", _PAST.isoformat(), "2026-01-01T00:15:00+00:00"),
        ("America/New_York", "0 23/6 * * *", _PAST.isoformat(), "2026-01-01T05:00:00+00:00"),
        ("America/New_York", "0 0 31/5 2 *", _PAST.isoformat(), "2026-02-01T05:00:00+00:00"),
        ("America/New_York", "0 0 * 12/3 *", _PAST.isoformat(), "2026-01-01T05:00:00+00:00"),
        ("America/New_York", "0 0 * DEC/3 *", _PAST.isoformat(), "2026-01-01T05:00:00+00:00"),
        ("America/New_York", "0 9 * * 6/1", _PAST.isoformat(), "2026-01-01T14:00:00+00:00"),
        ("America/New_York", "0 9 * * SAT/1", _PAST.isoformat(), "2026-01-01T14:00:00+00:00"),
        ("America/New_York", "0 9 * * 7/1", _PAST.isoformat(), "2026-01-01T14:00:00+00:00"),
        ("America/New_York", "0,59/15 * * * *", _PAST.isoformat(), "2026-01-01T00:15:00+00:00"),
        ("America/New_York", "0 9 * * *", "2026-03-07T14:00:00+00:00", "2026-03-08T13:00:00+00:00"),
        ("America/New_York", "0 9 * * *", "2026-03-08T13:00:00+00:00", "2026-03-09T13:00:00+00:00"),
        ("America/New_York", "0 9 * * *", "2026-10-31T13:00:00+00:00", "2026-11-01T14:00:00+00:00"),
        ("America/New_York", "0 9 * * *", "2026-11-01T14:00:00+00:00", "2026-11-02T14:00:00+00:00"),
        (
            "America/New_York",
            "30 2 * * *",
            "2026-03-07T07:30:00+00:00",
            "2026-03-08T07:00:00+00:00",
        ),
        (
            "America/New_York",
            "30 1 * * *",
            "2026-11-01T05:30:00+00:00",
            "2026-11-01T06:30:00+00:00",
        ),
        ("UTC", "0 9 * * *", "2026-03-07T09:00:00+00:00", "2026-03-08T09:00:00+00:00"),
        ("UTC", "0 9 * * *", "2026-10-31T09:00:00+00:00", "2026-11-01T09:00:00+00:00"),
    ],
)
def test_cron_next_fire_time(timezone: str, cron: str, start: str, expected: str) -> None:
    moment = datetime.fromisoformat(start)
    job = Job(
        id=uuid.uuid4(),
        kind="probe",
        payload={},
        due_at=moment,
        cron=cron,
        timezone=timezone,
        key=None,
        metadata={},
        attempts=0,
        created_at=moment,
    )
    assert job.next_fire_after(moment) == datetime.fromisoformat(expected)


@pytest.mark.usefixtures("registry_db")
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


@pytest.mark.usefixtures("registry_db")
async def test_a_key_replaces_the_job_queued_under_it() -> None:
    await queue.schedule("probe", {"v": 1}, due_at=_PAST, key="probe:a")
    await queue.schedule("probe", {"v": 2}, cron="*/5 * * * *", key="probe:a")

    [job] = await queue.find({})
    assert (job.payload, job.cron) == ({"v": 2}, "*/5 * * * *")


@pytest.mark.usefixtures("registry_db")
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


@pytest.mark.usefixtures("registry_db")
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


@pytest.mark.usefixtures("registry_db")
async def test_coalesced_work_shares_a_waiting_run_and_follows_a_running_one() -> None:
    later = datetime.now(UTC) + timedelta(hours=1)
    await queue.coalesce("probe", {"v": 1}, key="probe:d", due_at=later)
    await queue.coalesce("probe", {"v": 2}, key="probe:d", due_at=_PAST)
    await queue.coalesce("probe", {"v": 3}, key="probe:d", due_at=later)
    [waiting] = await queue.find({})
    assert (waiting.payload, waiting.due_at) == ({"v": 2}, _PAST)

    [running] = await queue.claim_due(1)
    await queue.coalesce("probe", {"v": 4}, key="probe:d", due_at=later)
    await queue.coalesce("probe", {"v": 5}, key="probe:d", due_at=_PAST)
    assert await queue.claim_due(1) == []
    assert await running.hold() == Hold.HELD
    await running.finish()

    [after] = await queue.claim_due(1)
    assert after.payload == {"v": 5}
    await after.finish()
    assert await queue.find({}) == []


@pytest.mark.usefixtures("registry_db")
async def test_jobs_are_found_and_cancelled_by_metadata() -> None:
    await queue.schedule("probe", metadata={"thread_id": "t1", "kind": "wake"}, due_at=_PAST)
    await queue.schedule("probe", metadata={"thread_id": "t2", "kind": "wake"}, due_at=_PAST)

    assert [job.metadata["thread_id"] for job in await queue.find({"thread_id": "t1"})] == ["t1"]
    assert await queue.cancel_matching({"thread_id": "t1"}) == 1
    assert [job.metadata["thread_id"] for job in await queue.find({"kind": "wake"})] == ["t2"]


@pytest.mark.usefixtures("registry_db")
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


@pytest.mark.usefixtures("registry_db", "memory_event_claims")
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
