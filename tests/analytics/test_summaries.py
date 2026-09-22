"""Daily summaries reconcile late events and retained projection history."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.analytics import ingestion, summaries
from agent.analytics.events import (
    EventEnvelope,
    EventName,
    FindingStatePayload,
    FindingSurfacedPayload,
    PROpenedPayload,
    PRStatePayload,
    RunCostRecordedPayload,
    RunStartedPayload,
)
from agent.database import postgres
from tests.analytics.helpers import DAY, event


@pytest.fixture(autouse=True)
async def summary_storage(analytics_db, monkeypatch):
    _, transaction = analytics_db
    monkeypatch.setattr(ingestion, "transaction", transaction)
    monkeypatch.setattr(summaries, "transaction", transaction)


async def flush_summaries():
    while await summaries.recompute_dirty_partitions(limit=100):
        pass


@pytest.mark.parametrize("failure", [None, "cancel", "publish"])
async def test_ingestion_progresses_during_summary_and_invalidation_survives_retry(
    analytics_db: tuple[UUID, Callable[[], AbstractAsyncContextManager[AsyncConnection]]],
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    workspace, transaction = analytics_db
    initial = event(
        workspace,
        EventName.RUN_STARTED,
        RunStartedPayload(model_attribution_quality="unavailable"),
        run_id=uuid4(),
    )
    await ingestion.ingest(initial)
    async with transaction() as conn:
        await conn.execute(text("DELETE FROM dirty_summary_partitions WHERE family <> 'additive'"))
        if failure == "publish":
            await conn.execute(
                text("""
                CREATE FUNCTION reject_summary() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'summary publish failed'; END; $$
                """)
            )
            await conn.execute(
                text("""
                CREATE TRIGGER reject_summary BEFORE DELETE ON dirty_summary_partitions
                FOR EACH ROW EXECUTE FUNCTION reject_summary()
                """)
            )
    computed, release = asyncio.Event(), asyncio.Event()
    compute = summaries._compute

    async def paused(conn: AsyncConnection, partition: dict[str, object]) -> dict[str, object]:
        payload = await compute(conn, partition)
        computed.set()
        await release.wait()
        return payload

    monkeypatch.setattr(summaries, "_compute", paused)
    worker = asyncio.create_task(summaries.recompute_dirty_partitions(limit=1))
    arrivals = [
        event(
            workspace,
            EventName.RUN_STARTED,
            RunStartedPayload(model_attribution_quality="unavailable"),
            run_id=uuid4(),
        )
        for _ in range(2)
    ]
    try:
        await asyncio.wait_for(computed.wait(), timeout=5)
        assert await asyncio.wait_for(summaries.recompute_dirty_partitions(limit=1), timeout=5) == 0
        if failure != "publish":
            assert await asyncio.wait_for(
                asyncio.gather(*(ingestion.ingest(item) for item in arrivals)), timeout=5
            ) == [True, True]
        if failure == "cancel":
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker
        else:
            release.set()
            if failure == "publish":
                with pytest.raises(DBAPIError, match="summary publish failed"):
                    await asyncio.wait_for(worker, timeout=5)
            else:
                assert await asyncio.wait_for(worker, timeout=5) == 1
    finally:
        release.set()
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    monkeypatch.setattr(summaries, "_compute", compute)
    async with transaction() as conn:
        assert (
            await conn.scalar(
                text("SELECT count(*) FROM dirty_summary_partitions WHERE family = 'additive'")
            )
            == 1
        )
        counters = await conn.scalar(text("SELECT counters FROM daily_summaries"))
        assert counters == ({"run.started": 1} if failure is None else None)
        if failure == "publish":
            await conn.execute(text("DROP TRIGGER reject_summary ON dirty_summary_partitions"))
    await asyncio.wait_for(flush_summaries(), timeout=5)
    for item in [initial, *(arrivals if failure != "publish" else [])]:
        assert not await ingestion.ingest(item)
    expected = 1 if failure == "publish" else 3
    async with transaction() as conn:
        assert (
            await conn.scalar(text("SELECT event_count FROM additive_event_projection")) == expected
        )
        assert await conn.scalar(
            text("SELECT counters FROM daily_summaries WHERE family = 'additive'")
        ) == {"run.started": expected}
        assert await conn.scalar(text("SELECT count(*) FROM dirty_summary_partitions")) == 0


async def test_cost_updates_recompute_the_run_start_day(analytics_db):
    workspace, transaction = analytics_db
    run_id = uuid4()
    await ingestion.ingest(
        event(
            workspace,
            EventName.RUN_STARTED,
            RunStartedPayload(model_attribution_quality="unavailable"),
            run_id=run_id,
        )
    )
    await flush_summaries()
    for revision in [1, 2]:
        await ingestion.ingest(
            event(
                workspace,
                EventName.RUN_COST_RECORDED,
                RunCostRecordedPayload(
                    cost_usd=revision,
                    status="complete",
                    source="provider",
                    observation_revision=revision,
                    observed_at=DAY + timedelta(days=revision),
                ),
                day=revision,
                run_id=run_id,
            )
        )
        await flush_summaries()
        async with transaction() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            "SELECT counters, sums FROM daily_summaries WHERE family = 'cost_completeness' "
                            "AND partition_date = :day"
                        ),
                        {"day": DAY.date()},
                    )
                )
                .mappings()
                .one()
            )
            assert row["counters"] == {"runs": 1, "known_cost_runs": 1}
            assert row["sums"] == {"cost_usd": revision}


@pytest.mark.parametrize("kind", ["pr", "finding"])
@pytest.mark.parametrize("flush_before_expiry", [False, True])
async def test_additive_totals_survive_raw_expiry(analytics_db, kind, flush_before_expiry):
    workspace, transaction = analytics_db
    subject_id = uuid4()
    if kind == "pr":
        opened = event(
            workspace,
            EventName.PR_OPENED,
            PROpenedPayload(opening_run_id=uuid4(), model_attribution_quality="unavailable"),
            pr_id=subject_id,
            repository_id=uuid4(),
        )
        outcome = event(
            workspace,
            EventName.PR_MERGED,
            PRStatePayload(),
            pr_id=subject_id,
            day=40,
        )
        family, state = "pr_open_cohort", "merged"
    else:
        opened = event(
            workspace,
            EventName.FINDING_SURFACED,
            FindingSurfacedPayload(severity="high", category="correctness"),
            finding_id=subject_id,
        )
        outcome = event(
            workspace,
            EventName.FINDING_RESOLVED,
            FindingStatePayload(),
            finding_id=subject_id,
            day=40,
        )
        family, state = "finding_surfaced_cohort", "resolved"
    await ingestion.ingest(opened)
    if flush_before_expiry:
        await flush_summaries()
    async with transaction() as conn:
        await conn.execute(text("DELETE FROM events"))
    await ingestion.ingest(outcome)
    # A previously unseen event for the expired day must still count exactly once.
    late = event(
        workspace,
        EventName.RUN_STARTED,
        RunStartedPayload(model_attribution_quality="unavailable"),
        run_id=uuid4(),
    )
    assert await ingestion.ingest(late)
    assert not await ingestion.ingest(late)
    await flush_summaries()
    async with transaction() as conn:
        counters = await conn.scalar(
            text(
                "SELECT counters FROM daily_summaries WHERE family = 'additive' "
                "AND partition_date = :day"
            ),
            {"day": DAY.date()},
        )
        assert counters == {opened.event_name.value: 1, "run.started": 1}
        counters = await conn.scalar(
            text(
                "SELECT counters FROM daily_summaries WHERE family = :family "
                "AND partition_date = :day"
            ),
            {"family": family, "day": DAY.date()},
        )
        assert counters == {state: 1}


@pytest.mark.parametrize("expired_count", [0, 1, 2])
async def test_additive_migration_preserves_expired_and_unsummarized_totals(
    analytics_db, expired_count
):
    workspace, transaction = analytics_db
    initial = []
    for day in (0, 0, 1):
        item = event(
            workspace,
            EventName.RUN_STARTED,
            RunStartedPayload(model_attribution_quality="unavailable"),
            run_id=uuid4(),
            day=day,
        )
        initial.append(item)
        await ingestion.ingest(item)
    await flush_summaries()
    async with transaction() as conn:
        for item in initial[:expired_count]:
            await conn.execute(
                text("DELETE FROM events WHERE event_id = :event_id"),
                {"event_id": item.event_id},
            )
        await conn.execute(
            text(
                "INSERT INTO daily_summaries (workspace_id, summary_version, family, "
                "partition_date, counters, data_watermark, recomputed_at) SELECT workspace_id, "
                "2, family, partition_date, counters, data_watermark, recomputed_at "
                "FROM daily_summaries WHERE family = 'additive'"
            )
        )
    for day in (0, 1, 2):
        # Queue creation can predate the summary even though ingestion happens afterward.
        late = event(
            workspace,
            EventName.RUN_STARTED,
            RunStartedPayload(model_attribution_quality="unavailable"),
            run_id=uuid4(),
            day=day,
            recorded_at=DAY,
        )
        assert await ingestion.ingest(late)
        assert not await ingestion.ingest(late)
    async with transaction() as conn:
        await conn.execute(text("DROP TABLE additive_event_projection"))
        migrations = postgres.load_migrations()
        await conn.run_sync(postgres.execute_revision, migrations, "0006")
        await conn.run_sync(postgres.execute_revision, migrations, "0006")
        rows = (
            await conn.execute(
                text(
                    "SELECT partition_date, event_count FROM additive_event_projection ORDER BY partition_date"
                )
            )
        ).all()
        assert rows == [
            ((DAY + timedelta(days=day)).date(), count) for day, count in enumerate((3, 2, 1))
        ]
    await flush_summaries()
    async with transaction() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT partition_date, (counters->>'run.started')::bigint "
                    "FROM daily_summaries WHERE family = 'additive' AND summary_version = 1 "
                    "ORDER BY partition_date"
                )
            )
        ).all()
        assert rows == [
            ((DAY + timedelta(days=day)).date(), count) for day, count in enumerate((3, 2, 1))
        ]


async def test_resolution_latency_survives_reopening_and_raw_expiry(analytics_db):
    workspace, transaction = analytics_db
    finding_id = uuid4()
    await ingestion.ingest(
        event(
            workspace,
            EventName.FINDING_SURFACED,
            FindingSurfacedPayload(severity="high", category="correctness"),
            finding_id=finding_id,
        )
    )
    await ingestion.ingest(
        event(
            workspace,
            EventName.FINDING_RESOLVED,
            FindingStatePayload(),
            finding_id=finding_id,
            day=1,
        )
    )
    await flush_summaries()
    async with transaction() as conn:
        await conn.execute(text("DELETE FROM events"))
    for day, name in [(2, EventName.FINDING_REOPENED), (3, EventName.FINDING_DISMISSED)]:
        await ingestion.ingest(
            event(
                workspace,
                name,
                FindingStatePayload(),
                finding_id=finding_id,
                day=day,
            )
        )
    await flush_summaries()
    async with transaction() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT histogram_bounds, histogram_counts FROM daily_summaries "
                        "WHERE family = 'latency_histogram' AND partition_date = :day"
                    ),
                    {"day": DAY.date()},
                )
            )
            .mappings()
            .one()
        )
        assert sum(row["histogram_counts"]) == 1
        bucket = row["histogram_counts"].index(1)
        latency_ms = timedelta(days=1).total_seconds() * 1000
        if bucket:
            assert row["histogram_bounds"][bucket - 1] < latency_ms
        if bucket < len(row["histogram_bounds"]):
            assert latency_ms <= row["histogram_bounds"][bucket]


@pytest.mark.parametrize("expire_raw_events", [False, True])
async def test_earlier_run_start_moves_cost_and_membership_out_of_previous_day(
    analytics_db, expire_raw_events
):
    workspace, transaction = analytics_db
    moved_run, other_run, moved_person, other_person = (uuid4() for _ in range(4))
    for run_id, user_id, cost in [(moved_run, moved_person, 5), (other_run, other_person, 2)]:
        await ingestion.ingest(
            event(
                workspace,
                EventName.RUN_STARTED,
                RunStartedPayload(model_attribution_quality="unavailable"),
                day=1,
                run_id=run_id,
                user_id=user_id,
            )
        )
        await ingestion.ingest(
            event(
                workspace,
                EventName.RUN_COST_RECORDED,
                RunCostRecordedPayload(
                    cost_usd=cost,
                    status="complete",
                    source="provider",
                    observation_revision=1,
                    observed_at=DAY + timedelta(days=2),
                ),
                day=2,
                run_id=run_id,
            )
        )
    await flush_summaries()
    if expire_raw_events:
        async with transaction() as conn:
            await conn.execute(text("DELETE FROM events"))
    await ingestion.ingest(
        event(
            workspace,
            EventName.RUN_STARTED,
            RunStartedPayload(model_attribution_quality="unavailable"),
            run_id=moved_run,
            user_id=moved_person,
        )
    )
    await flush_summaries()
    async with transaction() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT family, partition_date, counters, sums, exact_members FROM daily_summaries "
                        "WHERE family IN ('cost_completeness', 'distinct_membership')"
                    )
                )
            )
            .mappings()
            .all()
        )
        by_day = {(row["family"], row["partition_date"]): row for row in rows}
        for day, person, cost in [(0, moved_person, 5), (1, other_person, 2)]:
            date = (DAY + timedelta(days=day)).date()
            cost_row = by_day["cost_completeness", date]
            assert cost_row["counters"] == {"runs": 1, "known_cost_runs": 1}
            assert cost_row["sums"] == {"cost_usd": cost}
            assert by_day["distinct_membership", date]["exact_members"] == [person]


async def test_summary_batch_releases_completed_partition_before_next_compute(
    analytics_db: tuple[UUID, Callable[[], AbstractAsyncContextManager[AsyncConnection]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, transaction = analytics_db
    for day in (0, 1):
        await ingestion.ingest(
            event(
                workspace,
                EventName.RUN_STARTED,
                RunStartedPayload(model_attribution_quality="unavailable"),
                run_id=uuid4(),
                day=day,
            )
        )
    async with transaction() as conn:
        await conn.execute(text("DELETE FROM dirty_summary_partitions WHERE family <> 'additive'"))
        await conn.execute(
            text("UPDATE dirty_summary_partitions SET dirty_since = partition_date::timestamptz")
        )
    computing_second = asyncio.Event()
    release_second = asyncio.Event()
    compute = summaries._compute

    async def pause_second(
        conn: AsyncConnection, partition: dict[str, object]
    ) -> dict[str, object]:
        if partition["partition_date"] == (DAY + timedelta(days=1)).date():
            computing_second.set()
            await release_second.wait()
        return await compute(conn, partition)

    monkeypatch.setattr(summaries, "_compute", pause_second)
    worker = asyncio.create_task(summaries.recompute_dirty_partitions(limit=2))
    try:
        await asyncio.wait_for(computing_second.wait(), timeout=5)
        async with transaction() as conn:
            counters = await conn.scalar(
                text("SELECT counters FROM daily_summaries WHERE partition_date = :day"),
                {"day": DAY.date()},
            )
            assert counters == {"run.started": 1}
            assert (
                await conn.scalar(
                    text(
                        "SELECT count(*) FROM dirty_summary_partitions WHERE partition_date = :day"
                    ),
                    {"day": DAY.date()},
                )
                == 0
            )
        late = event(
            workspace,
            EventName.RUN_STARTED,
            RunStartedPayload(model_attribution_quality="unavailable"),
            run_id=uuid4(),
        )
        assert await asyncio.wait_for(ingestion.ingest(late), timeout=5)
    finally:
        release_second.set()
        await worker
    await flush_summaries()
    async with transaction() as conn:
        counters = await conn.scalar(
            text(
                "SELECT counters FROM daily_summaries WHERE family = 'additive' "
                "AND partition_date = :day"
            ),
            {"day": DAY.date()},
        )
        assert counters == {"run.started": 2}


async def test_concurrent_dirty_dates_and_families_do_not_deadlock(
    analytics_db: tuple[UUID, Callable[[], AbstractAsyncContextManager[AsyncConnection]]],
) -> None:
    workspace, transaction = analytics_db
    # Choose dates whose unsorted iteration conflicts, regardless of Python's hash seed.
    first_day = DAY.date()
    second_offset = next(
        offset
        for offset in range(1, 366)
        if list({first_day, (DAY + timedelta(days=offset)).date()})
        != list({(DAY + timedelta(days=offset)).date(), first_day})
    )
    second_day = (DAY + timedelta(days=second_offset)).date()
    first_run, second_run = uuid4(), uuid4()
    async with transaction() as conn:
        await conn.execute(
            text(
                "INSERT INTO run_projection (workspace_id, run_id, started_at) "
                "VALUES (:workspace, :first_run, :second_day), "
                "(:workspace, :second_run, :first_day)"
            ),
            {
                "workspace": workspace,
                "first_run": first_run,
                "second_run": second_run,
                "first_day": DAY,
                "second_day": DAY + timedelta(days=second_offset),
            },
        )
        await conn.execute(
            text("""
            CREATE FUNCTION pause_first_dirty_write() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF current_setting('test.pause_dirty', true) = 'on' THEN
                    PERFORM set_config('test.pause_dirty', 'off', true);
                    PERFORM pg_advisory_xact_lock(173492856);
                END IF;
                RETURN NEW;
            END $$
        """)
        )
        await conn.execute(
            text("""
            CREATE TRIGGER pause_dirty AFTER INSERT OR UPDATE ON dirty_summary_partitions
            FOR EACH ROW EXECUTE FUNCTION pause_first_dirty_write()
        """)
        )
    first = event(
        workspace,
        EventName.RUN_STARTED,
        RunStartedPayload(model_attribution_quality="unavailable"),
        run_id=first_run,
    )
    second = event(
        workspace,
        EventName.RUN_STARTED,
        RunStartedPayload(model_attribution_quality="unavailable"),
        run_id=second_run,
        day=second_offset,
    )
    pids: asyncio.Queue[int] = asyncio.Queue()

    async def dirty(item: EventEnvelope, *, pause: bool) -> None:
        async with transaction() as conn:
            if pause:
                await conn.execute(text("SELECT set_config('test.pause_dirty', 'on', true)"))
            pid = await conn.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int)
            await pids.put(pid)
            await summaries.mark_dirty(conn, item)

    async def wait_for_lock(conn: AsyncConnection, pid: int) -> None:
        async with asyncio.timeout(5):
            while not await conn.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = :pid AND NOT granted)"),
                {"pid": pid},
            ):
                await asyncio.sleep(0.01)

    tasks: list[asyncio.Task[None]] = []
    try:
        async with transaction() as gate:
            await gate.execute(text("SELECT pg_advisory_xact_lock(173492856)"))
            tasks.append(asyncio.create_task(dirty(first, pause=True)))
            await wait_for_lock(gate, await pids.get())
            tasks.append(asyncio.create_task(dirty(second, pause=False)))
            await wait_for_lock(gate, await pids.get())
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    async with transaction() as conn:
        rows = (
            await conn.execute(text("SELECT family, partition_date FROM dirty_summary_partitions"))
        ).all()
        assert set(rows) == {
            (family, day)
            for family in ("additive", "cost_completeness", "distinct_membership")
            for day in (first_day, second_day)
        }
