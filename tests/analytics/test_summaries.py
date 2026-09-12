"""Daily summaries reconcile late events and retained projection history."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from agent.analytics import ingestion, summaries
from agent.analytics.events import (
    EventName,
    FindingStatePayload,
    FindingSurfacedPayload,
    PROpenedPayload,
    PRStatePayload,
    RunCostRecordedPayload,
    RunStartedPayload,
)
from agent.database import analytics as database
from tests.analytics.helpers import DAY, event


@pytest.fixture(autouse=True)
async def summary_storage(analytics_db, monkeypatch):
    _, transaction = analytics_db
    monkeypatch.setattr(ingestion, "transaction", transaction)
    monkeypatch.setattr(summaries, "transaction", transaction)


async def flush_summaries():
    while await summaries.recompute_dirty_partitions(limit=100):
        pass


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
        schema = await conn.scalar(text("SELECT current_schema()"))
        revision = database._load_migrations().get_revision("0006")
        script = revision.module.SQL.replace("open_swe", schema)
        raw = await conn.get_raw_connection()
        await raw.driver_connection.execute(script)
        await raw.driver_connection.execute(script)
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
