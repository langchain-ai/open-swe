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


async def _ingest_model_effort_prs(efforts: list[str]) -> None:
    from agent.analytics import ingestion
    from agent.analytics.events import RunStartedPayload

    workspace = database.workspace_id()
    model_id = uuid4()
    repository_id = uuid4()
    for effort in efforts:
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


async def test_merge_rates_suppress_effort_breakdown_below_threshold(reporting_db, monkeypatch):
    monkeypatch.setenv("ANALYTICS_MIN_COHORT_SIZE", "3")
    await _ingest_model_effort_prs(["high", "high", "high", "low"])

    report = await queries.pr_merge_rate_by_model(period="all")
    cohort = report["cohorts"][0]
    assert cohort["cohort_size"] == 4
    assert cohort["efforts"] == []

    report = await queries.pr_merge_rate_by_model(period="all", admin=True)
    cohort = report["cohorts"][0]
    assert cohort["cohort_size"] == 4
    assert [(effort["effort"], effort["cohort_size"]) for effort in cohort["efforts"]] == [
        ("high", 3),
        ("low", 1),
    ]


async def test_merge_rates_keep_efforts_when_all_groups_meet_threshold(reporting_db, monkeypatch):
    monkeypatch.setenv("ANALYTICS_MIN_COHORT_SIZE", "3")
    await _ingest_model_effort_prs(["high", "high", "high", "low", "low", "low"])

    report = await queries.pr_merge_rate_by_model(period="all")
    cohort = report["cohorts"][0]
    assert cohort["cohort_size"] == 6
    assert [(effort["effort"], effort["cohort_size"]) for effort in cohort["efforts"]] == [
        ("high", 3),
        ("low", 3),
    ]


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
    for age, outcome, merge_days in [
        (30, EventName.PR_MERGED, 1.0),
        (2, EventName.PR_CLOSED_WITHOUT_MERGE, None),
        (14, EventName.PR_MERGED, 3.0),
        (13, None, None),
        (-1, None, None),
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
                    occurred_at=opened_at + timedelta(days=merge_days or 1),
                    environment="test",
                    payload=PRStatePayload(),
                    pr_id=pr_id,
                )
            )
    report = await queries.pr_merge_rate_by_model(period="all", maturity_days=14, admin=True)
    assert len(report["cohorts"]) == 1
    cohort = report["cohorts"][0]
    assert cohort["cohort_size"] == 4
    assert cohort["merged"] == 2
    assert cohort["closed_without_merge"] == 1
    assert cohort["waiting"] == 1
    assert cohort["mature_pending"] == 0
    assert cohort["decided_denominator"] == 3
    assert cohort["decided_merge_rate"] == 2 / 3
    assert cohort["mature_denominator"] == 3
    assert cohort["mature_cohort_merge_share"] == 2 / 3
    assert cohort["avg_merge_seconds"] == 2 * 86400

    report = await queries.pr_merge_rate_by_model(period="7d", admin=True)
    cohort = report["cohorts"][0]
    assert cohort["cohort_size"] == 1
    assert cohort["decided_merge_rate"] == 0
    assert cohort["avg_merge_seconds"] is None


async def test_pr_costs_sum_lifetime_threads_and_weight_prs_across_efforts(reporting_db):
    workspace = database.workspace_id()
    model = uuid4()
    threads = [uuid4(), uuid4(), uuid4()]
    runs = [uuid4() for _ in range(4)]
    now = datetime.now(UTC)
    async with postgres.transaction() as conn:
        for run, thread, cost, age, effort in [
            (runs[0], threads[0], 2, 40, "high"),
            (runs[1], threads[0], 8, 0, "low"),
            (runs[2], threads[1], 0, 1, "low"),
            (runs[3], threads[2], 100, 40, "low"),
        ]:
            await conn.execute(
                text(
                    "INSERT INTO run_projection "
                    "(workspace_id, run_id, thread_id, started_at, configured_effort, configured_model_id) "
                    "VALUES (:w, :r, :t, :started, :effort, :model)"
                ),
                {
                    "w": workspace,
                    "r": run,
                    "t": thread,
                    "started": now - timedelta(days=age),
                    "effort": effort,
                    "model": model if run != runs[1] else uuid4(),
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO latest_cost_projection "
                    "(workspace_id, run_id, observation_revision, observed_at, status, cost_usd, source, event_id) "
                    "VALUES (:w, :r, 1, :now, 'complete', :cost, 'test', :event)"
                ),
                {"w": workspace, "r": run, "now": now, "cost": cost, "event": uuid4()},
            )
        for run, state, age in [
            (runs[0], "merged", 2),
            (runs[0], "closed_without_merge", 2),
            (runs[2], "open", 1),
            (runs[3], "merged", 40),
        ]:
            await conn.execute(
                text(
                    "INSERT INTO pr_projection "
                    "(workspace_id, pr_id, repository_id, opening_run_id, originating_model_id, "
                    "model_attribution_quality, opened_at, current_state) "
                    "VALUES (:w, :pr, :repo, :run, :model, 'configured', :opened, :state)"
                ),
                {
                    "w": workspace,
                    "pr": uuid4(),
                    "repo": uuid4(),
                    "run": run,
                    "model": model,
                    "opened": now - timedelta(days=age),
                    "state": state,
                },
            )
    cohort = (await queries.pr_merge_rate_by_model(period="7d", admin=True))["cohorts"][0]
    assert cohort["cohort_size"] == 3
    assert cohort["prs_with_complete_cost"] == 3
    assert cohort["avg_pr_cost_usd"] == pytest.approx(20 / 3)
    assert [(e["effort"], e["avg_pr_cost_usd"]) for e in cohort["efforts"]] == [
        ("high", 10),
        ("low", 0),
    ]
    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE latest_cost_projection SET cost_usd = 11 WHERE run_id = :run"),
            {"run": runs[1]},
        )
    cohort = (await queries.pr_merge_rate_by_model(period="7d", admin=True))["cohorts"][0]
    assert cohort["avg_pr_cost_usd"] == pytest.approx(26 / 3)
    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE latest_cost_projection SET status = 'partial' WHERE run_id = :run"),
            {"run": runs[2]},
        )
    cohort = (await queries.pr_merge_rate_by_model(period="7d", admin=True))["cohorts"][0]
    assert cohort["prs_with_complete_cost"] == 2
    assert cohort["avg_pr_cost_usd"] == 13
    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE latest_cost_projection SET status = 'complete' WHERE run_id = :run"),
            {"run": runs[2]},
        )
        await conn.execute(
            text("UPDATE latest_cost_projection SET status = 'partial' WHERE run_id = :run"),
            {"run": runs[1]},
        )
    cohort = (await queries.pr_merge_rate_by_model(period="7d", admin=True))["cohorts"][0]
    assert cohort["prs_with_complete_cost"] == 1
    assert cohort["avg_pr_cost_usd"] is None


@pytest.mark.parametrize(
    "missing", ["observation", "partial", "unavailable", "null_cost", "thread", "run"]
)
async def test_pr_average_is_null_when_any_thread_cost_is_incomplete(reporting_db, missing):
    model, thread, opening, later = [uuid4() for _ in range(4)]
    workspace = database.workspace_id()
    now = datetime.now(UTC)
    async with postgres.transaction() as conn:
        for run in (opening, later):
            await conn.execute(
                text(
                    "INSERT INTO run_projection (workspace_id, run_id, thread_id, started_at) "
                    "VALUES (:w, :r, :t, :now)"
                ),
                {"w": workspace, "r": run, "t": thread, "now": now},
            )
            await conn.execute(
                text(
                    "INSERT INTO latest_cost_projection "
                    "(workspace_id, run_id, observation_revision, observed_at, status, cost_usd, source, event_id) "
                    "VALUES (:w, :r, 1, :now, 'complete', 3, 'test', :event)"
                ),
                {"w": workspace, "r": run, "now": now, "event": uuid4()},
            )
        await conn.execute(
            text(
                "INSERT INTO pr_projection "
                "(workspace_id, pr_id, repository_id, opening_run_id, originating_model_id, "
                "model_attribution_quality, opened_at, current_state) "
                "VALUES (:w, :pr, :repo, :run, :model, 'configured', :now, 'open')"
            ),
            {
                "w": workspace,
                "pr": uuid4(),
                "repo": uuid4(),
                "run": opening,
                "model": model,
                "now": now,
            },
        )
        if missing == "observation":
            await conn.execute(
                text("DELETE FROM latest_cost_projection WHERE run_id = :r"), {"r": later}
            )
        elif missing in {"partial", "unavailable", "null_cost"}:
            await conn.execute(
                text(
                    "UPDATE latest_cost_projection SET status = :status, cost_usd = :cost WHERE run_id = :r"
                ),
                {
                    "r": later,
                    "status": "complete" if missing == "null_cost" else missing,
                    "cost": None if missing == "null_cost" else 3,
                },
            )
        elif missing == "thread":
            await conn.execute(
                text("UPDATE run_projection SET thread_id = NULL WHERE run_id = :r"), {"r": opening}
            )
        else:
            await conn.execute(text("DELETE FROM run_projection WHERE run_id = :r"), {"r": opening})
    cohort = (await queries.pr_merge_rate_by_model(period="all", admin=True))["cohorts"][0]
    assert cohort["cohort_size"] == 1
    assert cohort["prs_with_complete_cost"] == 0
    assert cohort["avg_pr_cost_usd"] is None
    assert cohort["efforts"][0]["avg_pr_cost_usd"] is None
