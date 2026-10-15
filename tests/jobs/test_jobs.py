import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from openswe.database.postgres import transaction
from openswe.jobs import queue
from openswe.jobs.queue import Job
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


async def test_jobs_are_found_and_cancelled_by_metadata() -> None:
    await queue.schedule("probe", metadata={"thread_id": "t1", "kind": "wake"}, due_at=_PAST)
    await queue.schedule("probe", metadata={"thread_id": "t2", "kind": "wake"}, due_at=_PAST)

    assert [job.metadata["thread_id"] for job in await queue.find({"thread_id": "t1"})] == ["t1"]
    assert await queue.cancel_matching({"thread_id": "t1"}) == 1
    assert [job.metadata["thread_id"] for job in await queue.find({"kind": "wake"})] == ["t2"]
