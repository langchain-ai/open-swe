"""Cohort reports distinguish capture, delivery, and disclosure state."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from agent.analytics import database, outbox, queries
from agent.analytics.events import EventName, PROpenedPayload, make_event


@pytest.fixture
async def reporting_db(deployment_db):
    await database.migrate()
    async with database.transaction() as conn:
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
        pr_id=uuid4(),
        repository_id=uuid4(),
    )
    assert await outbox.enqueue(opened)
    report = await queries.pr_merge_rate_by_model(period="all")
    assert report["status"] == "no_prs"
    assert report["collection_started_at"] is not None
    assert report["last_processed_at"] is None
    assert report["has_pending_events"]

    async with database.transaction() as conn:
        await conn.execute(text("UPDATE outbox SET state = 'dead_letter'"))
    report = await queries.pr_merge_rate_by_model(period="all")
    assert report["has_failed_events"]
    assert not report["has_pending_events"]
    async with database.transaction() as conn:
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
    assert report["status"] == "ready"
    assert report["cohorts"][0]["cohort_size"] == 1

    async with database.transaction() as conn:
        await conn.execute(text("DELETE FROM events"))
        await conn.execute(text("DELETE FROM ingestion_receipts"))
        await conn.execute(text("DELETE FROM outbox"))
    await database.close()
    await database.migrate()
    report = await queries.pr_merge_rate_by_model(period="all", admin=True)
    assert report["status"] == "ready"
    assert datetime.fromisoformat(report["last_processed_at"]) == processed_at


async def test_merge_rates_separate_decisions_maturity_and_waiting(reporting_db, monkeypatch):
    from agent.analytics import ingestion
    from agent.analytics.events import PRStatePayload

    as_of = datetime(2026, 9, 11, tzinfo=UTC)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return as_of

    monkeypatch.setattr(queries, "datetime", FixedDatetime)
    model_id = uuid4()
    workspace = database.workspace_id()
    for age, outcome in [
        (30, EventName.PR_MERGED),
        (2, EventName.PR_CLOSED_WITHOUT_MERGE),
        (14, None),
        (13, None),
        (-1, None),
    ]:
        pr_id = uuid4()
        opened_at = as_of - timedelta(days=age)
        await ingestion.ingest(
            make_event(
                workspace_id=workspace,
                event_name=EventName.PR_OPENED,
                producer="test",
                producer_event_id=str(uuid4()),
                occurred_at=opened_at,
                environment="test",
                payload=PROpenedPayload(
                    opening_run_id=uuid4(),
                    originating_model_id=model_id,
                    model_attribution_quality="configured",
                ),
                pr_id=pr_id,
                repository_id=uuid4(),
            )
        )
        if outcome:
            await ingestion.ingest(
                make_event(
                    workspace_id=workspace,
                    event_name=outcome,
                    producer="test",
                    producer_event_id=str(uuid4()),
                    occurred_at=opened_at + timedelta(days=1),
                    environment="test",
                    payload=PRStatePayload(),
                    pr_id=pr_id,
                )
            )
    report = await queries.pr_merge_rate_by_model(period="all", maturity_days=14, admin=True)
    assert len(report["cohorts"]) == 1
    cohort = report["cohorts"][0]
    assert cohort["cohort_size"] == 4
    assert cohort["merged"] == 1
    assert cohort["closed_without_merge"] == 1
    assert cohort["waiting"] == 1
    assert cohort["mature_pending"] == 1
    assert cohort["decided_denominator"] == 2
    assert cohort["decided_merge_rate"] == 0.5
    assert cohort["mature_denominator"] == 3
    assert cohort["mature_cohort_merge_share"] == 1 / 3

    report = await queries.pr_merge_rate_by_model(period="7d", admin=True)
    assert report["cohorts"][0]["cohort_size"] == 1
    assert report["cohorts"][0]["decided_merge_rate"] == 0
