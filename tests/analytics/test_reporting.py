"""Cohort reports distinguish capture, delivery, and disclosure state."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from agent.analytics import outbox, queries
from agent.analytics.events import EventName, PROpenedPayload, make_event
from agent.database import analytics as database
from agent.database import postgres
from tests.analytics.conftest import initialize_database


@pytest.fixture
async def reporting_db(deployment_db):
    await initialize_database()
    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE deployment_metadata SET reporting_cutover_at = :cutover"),
            {"cutover": datetime(2026, 1, 1, tzinfo=UTC)},
        )


async def test_report_distinguishes_capture_delivery_period_and_suppression(
    reporting_db, monkeypatch
):
    monkeypatch.setenv("ANALYTICS_MIN_COHORT_SIZE", "2")
    report = await queries.pr_merge_rate_by_model(period="all")
    assert report["status"] == "not_started"
    assert report["collection_started_at"] is None
    assert report["last_processed_at"] is None
    assert not report["has_pending_events"]

    opened = make_event(
        workspace_id=database.workspace_id(),
        event_name=EventName.PR_OPENED,
        producer="test",
        producer_event_id=str(uuid4()),
        occurred_at=datetime.now(UTC) - timedelta(days=60),
        environment="test",
        payload=PROpenedPayload(opening_run_id=uuid4(), model_attribution_quality="unavailable"),
        thread_id=uuid4(),
        pr_id=uuid4(),
        repository_id=uuid4(),
    )
    assert await outbox.enqueue(opened)
    report = await queries.pr_merge_rate_by_model(period="all")
    assert report["status"] == "no_prs"
    assert report["collection_started_at"] is not None
    assert report["last_processed_at"] is None
    assert report["has_pending_events"]

    async with postgres.transaction() as conn:
        await conn.execute(text("UPDATE outbox SET state = 'dead_letter'"))
    report = await queries.pr_merge_rate_by_model(period="all")
    assert report["has_failed_events"]
    assert not report["has_pending_events"]
    async with postgres.transaction() as conn:
        await conn.execute(text("UPDATE outbox SET state = 'pending'"))
    before_delivery = datetime.now(UTC)
    assert await outbox.deliver_batch() == 1
    report = await queries.pr_merge_rate_by_model(period="all")
    assert report["status"] == "suppressed"
    assert report["cohorts"] == []
    assert report["data_source"] == "event_projections"
    processed_at = datetime.fromisoformat(report["last_processed_at"])
    assert before_delivery <= processed_at <= datetime.now(UTC)
    assert not report["has_pending_events"]
    assert not report["has_failed_events"]
    assert report["completeness"] == "observed_events_only"

    report = await queries.pr_merge_rate_by_model(period="7d")
    assert report["status"] == "no_prs"
    assert report["cohorts"] == []
    report = await queries.pr_merge_rate_by_model(period="all", admin=True)
    assert report["status"] == "suppressed"
    assert report["cohorts"] == []
    assert report["unavailable_thread_ids"] == [str(opened.thread_id)]

    async with postgres.transaction() as conn:
        await conn.execute(text("DELETE FROM events"))
        await conn.execute(text("DELETE FROM ingestion_receipts"))
        await conn.execute(text("DELETE FROM outbox"))
    await postgres.close()
    await initialize_database()
    report = await queries.pr_merge_rate_by_model(period="all", admin=True)
    assert report["status"] == "suppressed"
    assert report["unavailable_thread_ids"] == []
    assert datetime.fromisoformat(report["last_processed_at"]) == processed_at


async def test_merge_rates_keep_models_without_directory_entries_separate(reporting_db):
    from agent.analytics import ingestion

    workspace = database.workspace_id()
    for model_id in (uuid4(), uuid4()):
        await ingestion.ingest(
            make_event(
                workspace_id=workspace,
                event_name=EventName.PR_OPENED,
                producer="test",
                producer_event_id=str(uuid4()),
                occurred_at=datetime.now(UTC) - timedelta(days=1),
                environment="test",
                payload=PROpenedPayload(
                    opening_run_id=uuid4(),
                    originating_model_id=model_id,
                    model_attribution_quality="configured",
                ),
                pr_id=uuid4(),
                repository_id=uuid4(),
            )
        )
    report = await queries.pr_merge_rate_by_model(period="all", admin=True)
    assert len(report["cohorts"]) == 2
    assert all(cohort["cohort_size"] == 1 for cohort in report["cohorts"])


async def test_merge_rates_group_efforts_under_model_privacy_cohorts(reporting_db):
    from agent.analytics import ingestion
    from agent.analytics.events import RunStartedPayload

    workspace = database.workspace_id()
    model_id = uuid4()
    repository_id = uuid4()
    for effort in ("low", "high", "high"):
        run_id = uuid4()
        await ingestion.ingest(
            make_event(
                workspace_id=workspace,
                event_name=EventName.RUN_STARTED,
                producer="test",
                producer_event_id=str(uuid4()),
                occurred_at=datetime.now(UTC) - timedelta(days=1),
                environment="test",
                payload=RunStartedPayload(
                    configured_model_id=model_id,
                    configured_effort=effort,
                    model_attribution_quality="configured",
                ),
                run_id=run_id,
            )
        )
        await ingestion.ingest(
            make_event(
                workspace_id=workspace,
                event_name=EventName.PR_OPENED,
                producer="test",
                producer_event_id=str(uuid4()),
                occurred_at=datetime.now(UTC) - timedelta(days=1),
                environment="test",
                payload=PROpenedPayload(
                    opening_run_id=run_id,
                    originating_model_id=model_id,
                    model_attribution_quality="configured",
                ),
                pr_id=uuid4(),
                repository_id=repository_id,
            )
        )
    report = await queries.pr_merge_rate_by_model(period="all", admin=True)
    cohort = report["cohorts"][0]
    assert cohort["cohort_size"] == 3
    assert [(effort["effort"], effort["cohort_size"]) for effort in cohort["efforts"]] == [
        ("high", 2),
        ("low", 1),
    ]


async def test_avg_time_to_pr_measures_opening_run_start_to_pr_creation(reporting_db):
    from agent.analytics import ingestion
    from agent.analytics.events import RunStartedPayload

    workspace = database.workspace_id()
    model_id = uuid4()
    opened = datetime.now(UTC) - timedelta(days=1)
    for run_start_offset_hours, open_offset_hours in [(3.0, 1.0), (2.0, 1.0)]:
        run_id = uuid4()
        await ingestion.ingest(
            make_event(
                workspace_id=workspace,
                event_name=EventName.RUN_STARTED,
                producer="test",
                producer_event_id=str(uuid4()),
                occurred_at=opened - timedelta(hours=run_start_offset_hours),
                environment="test",
                payload=RunStartedPayload(
                    configured_model_id=model_id,
                    model_attribution_quality="configured",
                ),
                run_id=run_id,
            )
        )
        await ingestion.ingest(
            make_event(
                workspace_id=workspace,
                event_name=EventName.PR_OPENED,
                producer="test",
                producer_event_id=str(uuid4()),
                occurred_at=opened - timedelta(hours=open_offset_hours),
                environment="test",
                payload=PROpenedPayload(
                    opening_run_id=run_id,
                    originating_model_id=model_id,
                    model_attribution_quality="configured",
                ),
                pr_id=uuid4(),
                repository_id=uuid4(),
            )
        )
    # Invalid pair: the opening run started after the PR was created.
    late_run_id = uuid4()
    await ingestion.ingest(
        make_event(
            workspace_id=workspace,
            event_name=EventName.RUN_STARTED,
            producer="test",
            producer_event_id=str(uuid4()),
            occurred_at=opened,
            environment="test",
            payload=RunStartedPayload(
                configured_model_id=model_id,
                model_attribution_quality="configured",
            ),
            run_id=late_run_id,
        )
    )
    await ingestion.ingest(
        make_event(
            workspace_id=workspace,
            event_name=EventName.PR_OPENED,
            producer="test",
            producer_event_id=str(uuid4()),
            occurred_at=opened - timedelta(hours=1),
            environment="test",
            payload=PROpenedPayload(
                opening_run_id=late_run_id,
                originating_model_id=model_id,
                model_attribution_quality="configured",
            ),
            pr_id=uuid4(),
            repository_id=uuid4(),
        )
    )
    # Missing pair: the opening run was never recorded.
    await ingestion.ingest(
        make_event(
            workspace_id=workspace,
            event_name=EventName.PR_OPENED,
            producer="test",
            producer_event_id=str(uuid4()),
            occurred_at=opened,
            environment="test",
            payload=PROpenedPayload(
                opening_run_id=uuid4(),
                originating_model_id=model_id,
                model_attribution_quality="configured",
            ),
            pr_id=uuid4(),
            repository_id=uuid4(),
        )
    )
    report = await queries.pr_merge_rate_by_model(period="all", admin=True)
    cohort = report["cohorts"][0]
    assert cohort["cohort_size"] == 4
    assert cohort["avg_delivery_seconds"] == (2 * 3600 + 3600) / 2
