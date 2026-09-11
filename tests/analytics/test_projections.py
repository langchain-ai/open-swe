"""Durable projections reconcile duplicate and out-of-order events."""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from agent.analytics import database, emitter, identity, ingestion
from agent.analytics.events import (
    EventName,
    FeedbackSubmittedPayload,
    FeedbackWithdrawnPayload,
    FindingStatePayload,
    FindingSurfacedPayload,
    PROpenedPayload,
    PRStatePayload,
    RunCompletedPayload,
    RunCostRecordedPayload,
    RunStartedPayload,
)
from tests.analytics.helpers import DAY, event


@pytest.fixture(autouse=True)
async def projection_storage(analytics_db, monkeypatch):
    _, transaction = analytics_db
    monkeypatch.setattr(ingestion, "transaction", transaction)


@pytest.mark.parametrize(
    ("version", "outcome_name", "state", "delivery"),
    [
        pytest.param(2, EventName.PR_MERGED, "merged", "outcome_first", id="merge-before-open"),
        pytest.param(None, EventName.PR_MERGED, "merged", "concurrent", id="concurrent-merge"),
        pytest.param(
            2,
            EventName.PR_CLOSED_WITHOUT_MERGE,
            "closed_without_merge",
            "opening_first",
            id="normal-close",
        ),
        pytest.param(
            None,
            EventName.PR_CLOSED_WITHOUT_MERGE,
            "closed_without_merge",
            "outcome_first",
            id="unversioned-close-before-open",
        ),
    ],
)
async def test_pr_outcome_survives_delivery_order(
    analytics_db, version, outcome_name, state, delivery
):
    workspace, transaction = analytics_db
    pr_id = uuid4()
    opened = event(
        workspace,
        EventName.PR_OPENED,
        PROpenedPayload(opening_run_id=uuid4(), model_attribution_quality="unavailable"),
        pr_id=pr_id,
        repository_id=uuid4(),
        source_version=1 if version else None,
    )
    outcome = event(
        workspace, outcome_name, PRStatePayload(), day=1, pr_id=pr_id, source_version=version
    )
    if delivery == "concurrent":
        await asyncio.gather(ingestion.ingest(outcome), ingestion.ingest(opened))
    else:
        for item in [outcome, opened] if delivery == "outcome_first" else [opened, outcome]:
            assert await ingestion.ingest(item)
    assert not await ingestion.ingest(outcome)
    assert not await ingestion.ingest(opened)
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM pr_projection"))).mappings().one()
        assert row["current_state"] == state
        assert row["opened_at"] == DAY
        assert row["outcome_at"] == (None if state == "open" else DAY + timedelta(days=1))


async def test_emitted_finding_links_to_published_review(analytics_db, monkeypatch):
    _, transaction = analytics_db

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return DAY

    monkeypatch.setattr(emitter, "datetime", FixedDatetime)
    monkeypatch.setenv("POSTGRES_URI", "postgresql://localhost/analytics_test")
    monkeypatch.setattr(emitter, "enqueue", ingestion.ingest)
    await emitter.review_published(
        thread_key="thread",
        owner="owner",
        repo="repo",
        number=1,
        head_sha="head",
        finding_count=1,
    )
    await emitter.finding_transition(
        thread_key="thread",
        finding_key="finding",
        head_sha="head",
        owner="owner",
        repo="repo",
        number=1,
        state="surfaced",
        severity="high",
        category="correctness",
        version="1",
    )
    async with transaction() as conn:
        assert (
            await conn.scalar(
                text(
                    "SELECT count(*) FROM finding_projection f JOIN review_projection r "
                    "USING (workspace_id, review_id, pr_id)"
                )
            )
            == 1
        )


async def test_directory_preserves_immutable_identity_after_login_change(analytics_db, monkeypatch):
    from agent.analytics import directory

    workspace, transaction = analytics_db
    monkeypatch.setenv("POSTGRES_URI", "postgresql://localhost/analytics_test")
    monkeypatch.setattr(directory, "transaction", transaction)
    current_person = identity.opaque_person("github", 123)
    other_person = identity.opaque_person("github", 456)
    await directory.upsert_person(
        provider="github",
        immutable_person_key=123,
        github_login="old-login",
        email="self@example.com",
    )
    await directory.upsert_person(
        provider="github", immutable_person_key=456, github_login="other", email="other@example.com"
    )
    for person in (current_person, other_person, other_person):
        await ingestion.ingest(
            event(
                workspace,
                EventName.RUN_STARTED,
                RunStartedPayload(model_attribution_quality="unavailable"),
                run_id=uuid4(),
                user_id=person,
            )
        )
    await directory.upsert_person(
        provider="github", immutable_person_key=123, github_login="new-login"
    )
    async with transaction() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT person_id, github_login, email FROM identity_directory "
                        "WHERE workspace_id = :workspace ORDER BY github_login"
                    ),
                    {"workspace": workspace},
                )
            )
            .mappings()
            .all()
        )
        assert [(row["person_id"], row["github_login"], row["email"]) for row in rows] == [
            (current_person, "new-login", "self@example.com"),
            (other_person, "other", "other@example.com"),
        ]
        assert (
            await conn.scalar(
                text(
                    "SELECT count(*) FROM run_projection r JOIN identity_directory d "
                    "ON r.workspace_id = d.workspace_id AND r.user_id = d.person_id"
                )
            )
            == 3
        )


@pytest.mark.parametrize("versioned", [False, True])
async def test_pr_reopening_rejects_delayed_close(analytics_db, versioned):
    workspace, transaction = analytics_db
    pr_id = uuid4()
    events = [
        event(
            workspace,
            EventName.PR_OPENED,
            PROpenedPayload(opening_run_id=uuid4(), model_attribution_quality="unavailable"),
            pr_id=pr_id,
            repository_id=uuid4(),
            source_version=0 if versioned else None,
        ),
        event(
            workspace,
            EventName.PR_CLOSED_WITHOUT_MERGE,
            PRStatePayload(),
            pr_id=pr_id,
            day=1,
            source_version=1 if versioned else None,
        ),
        event(
            workspace,
            EventName.PR_REOPENED,
            PRStatePayload(),
            pr_id=pr_id,
            day=2,
            source_version=2 if versioned else None,
        ),
    ]
    for index in (2, 0, 1):
        assert await ingestion.ingest(events[index])
    for item in events:
        assert not await ingestion.ingest(item)
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM pr_projection"))).mappings().one()
        assert row["current_state"] == "open"
        assert row["outcome_at"] is None
        assert row["latest_transition_at"] == DAY + timedelta(days=2)

    await ingestion.ingest(
        event(
            workspace,
            EventName.PR_MERGED,
            PRStatePayload(),
            pr_id=pr_id,
            day=3,
            source_version=3 if versioned else None,
        )
    )
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM pr_projection"))).mappings().one()
        assert row["current_state"] == "merged"
        assert row["outcome_at"] == DAY + timedelta(days=3)


async def test_pr_timestamp_migration_preserves_existing_reopen(analytics_db):
    workspace, transaction = analytics_db
    pr_id = uuid4()
    await ingestion.ingest(
        event(
            workspace,
            EventName.PR_OPENED,
            PROpenedPayload(opening_run_id=uuid4(), model_attribution_quality="unavailable"),
            pr_id=pr_id,
            repository_id=uuid4(),
        )
    )
    await ingestion.ingest(
        event(
            workspace,
            EventName.PR_REOPENED,
            PRStatePayload(),
            pr_id=pr_id,
            day=2,
        )
    )
    async with transaction() as conn:
        schema = await conn.scalar(text("SELECT current_schema()"))
        await conn.execute(text("ALTER TABLE pr_projection DROP COLUMN latest_transition_at"))
        migration = (
            Path(database.__file__).with_name("migrations") / "0002_pr_transition_timestamp.sql"
        )
        raw = await conn.get_raw_connection()
        await raw.driver_connection.execute(migration.read_text().replace("open_swe", schema))
    await ingestion.ingest(
        event(
            workspace,
            EventName.PR_CLOSED_WITHOUT_MERGE,
            PRStatePayload(),
            pr_id=pr_id,
            day=1,
        )
    )
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM pr_projection"))).mappings().one()
        assert row["current_state"] == "open"
        assert row["outcome_at"] is None
        assert row["latest_transition_at"] == DAY + timedelta(days=2)


@pytest.mark.parametrize(
    ("delivery", "versioned", "surface_first"),
    [
        pytest.param((0, 1, 2), False, True, id="chronological"),
        pytest.param((2, 0, 1), True, False, id="transitions-before-surface"),
        pytest.param((2, 1, 0), False, True, id="reverse-timestamps"),
        pytest.param("concurrent", True, True, id="concurrent-transitions"),
    ],
)
async def test_finding_history_survives_late_transitions(
    analytics_db, delivery, versioned, surface_first
):
    workspace, transaction = analytics_db
    finding_id = uuid4()
    surfaced = event(
        workspace,
        EventName.FINDING_SURFACED,
        FindingSurfacedPayload(severity="high", category="correctness"),
        finding_id=finding_id,
    )
    transitions = [
        event(
            workspace,
            name,
            FindingStatePayload(),
            finding_id=finding_id,
            day=day,
            source_version=day if versioned else None,
        )
        for day, name in [
            (1, EventName.FINDING_RESOLVED),
            (2, EventName.FINDING_REOPENED),
            (3, EventName.FINDING_DISMISSED),
        ]
    ]
    if surface_first:
        await ingestion.ingest(surfaced)
    if delivery == "concurrent":
        await asyncio.gather(*(ingestion.ingest(item) for item in transitions))
    else:
        for index in delivery:
            await ingestion.ingest(transitions[index])
    if not surface_first:
        await ingestion.ingest(surfaced)
    for item in transitions:
        assert not await ingestion.ingest(item)
    await ingestion.ingest(
        event(
            workspace,
            EventName.FINDING_SURFACED,
            surfaced.payload,
            finding_id=finding_id,
            day=4,
        )
    )
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM finding_projection"))).mappings().one()
        assert row["current_state"] == "dismissed"
        assert row["resolved_at"] == DAY + timedelta(days=1)
        assert row["dismissed_at"] == DAY + timedelta(days=3)
        assert row["latest_occurred_at"] == DAY + timedelta(days=3)
        assert row["reopened_count"] == 1


async def test_reopenings_accumulate_across_repeated_raw_expiry(analytics_db):
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
        event(workspace, EventName.FINDING_RESOLVED, FindingStatePayload(), finding_id=finding_id)
    )
    for index in range(1, 5):
        reopening = event(
            workspace,
            EventName.FINDING_REOPENED,
            FindingStatePayload(),
            finding_id=finding_id,
            day=index,
            source_version=index,
        )
        assert await ingestion.ingest(reopening)
        assert not await ingestion.ingest(reopening)
        async with transaction() as conn:
            assert await conn.scalar(text("SELECT reopened_count FROM finding_projection")) == index
            assert await conn.scalar(text("SELECT resolved_at FROM finding_projection")) == DAY
        if index >= 2:
            async with transaction() as conn:
                await conn.execute(text("DELETE FROM events"))


@pytest.mark.parametrize(
    "latest_version, delayed_version, delayed_day",
    [
        (10, 9, 20),
        (10, 10, 9),
        (None, None, 9),
        (10, None, 20),
    ],
)
async def test_stale_finding_transition_after_raw_expiry(
    analytics_db, latest_version, delayed_version, delayed_day
):
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
            day=10,
            source_version=latest_version,
        )
    )
    async with transaction() as conn:
        await conn.execute(text("DELETE FROM events"))
    await ingestion.ingest(
        event(
            workspace,
            EventName.FINDING_REOPENED,
            FindingStatePayload(),
            finding_id=finding_id,
            day=delayed_day,
            source_version=delayed_version,
        )
    )
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM finding_projection"))).mappings().one()
        assert row["current_state"] == "resolved"
        assert row["source_version"] == latest_version
        assert row["latest_occurred_at"] == DAY + timedelta(days=10)
        assert row["reopened_count"] == 1


@pytest.mark.parametrize("effective_first", [False, True])
async def test_late_run_start_restores_attribution(analytics_db, effective_first):
    workspace, transaction = analytics_db
    run_id, model_id = uuid4(), uuid4()
    await ingestion.ingest(
        event(workspace, EventName.RUN_COMPLETED, RunCompletedPayload(), run_id=run_id, day=1)
    )
    if effective_first:
        await ingestion.ingest(
            event(
                workspace,
                EventName.RUN_STARTED,
                RunStartedPayload(
                    effective_model_id=model_id, model_attribution_quality="effective"
                ),
                run_id=run_id,
            )
        )
    await ingestion.ingest(
        event(
            workspace,
            EventName.RUN_STARTED,
            RunStartedPayload(configured_model_id=model_id, model_attribution_quality="configured"),
            run_id=run_id,
        )
    )
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM run_projection"))).mappings().one()
        assert row["configured_model_id"] == model_id
        assert row["model_attribution_quality"] == (
            "effective" if effective_first else "configured"
        )
        assert row["technical_status"] == "completed"


@pytest.mark.parametrize(
    "amount",
    [None, 0],
)
async def test_cost_projection_distinguishes_unknown_and_zero_cost(analytics_db, amount):
    workspace, transaction = analytics_db
    user_id = uuid4()
    run_id = uuid4()
    await ingestion.ingest(
        event(
            workspace,
            EventName.RUN_STARTED,
            RunStartedPayload(model_attribution_quality="unavailable"),
            run_id=run_id,
            user_id=user_id,
        )
    )
    await ingestion.ingest(
        event(
            workspace,
            EventName.RUN_COST_RECORDED,
            RunCostRecordedPayload(
                cost_usd=amount,
                status="complete",
                source="langsmith",
                observation_revision=1,
                observed_at=DAY,
            ),
            run_id=run_id,
        )
    )
    async with transaction() as conn:
        row = (
            (await conn.execute(text("SELECT cost_usd, status FROM latest_cost_projection")))
            .mappings()
            .one()
        )
        assert row["cost_usd"] == amount
        assert row["status"] == "complete"


@pytest.mark.parametrize(
    "delivery",
    [
        pytest.param((0, 1, 2), id="submission-first"),
        pytest.param((2, 1, 0), id="withdrawals-before-submission"),
        pytest.param("concurrent", id="concurrent-withdrawals"),
    ],
)
async def test_feedback_withdrawal_survives_delivery_order(analytics_db, delivery):
    workspace, transaction = analytics_db
    submitted = event(
        workspace,
        EventName.FEEDBACK_SUBMITTED,
        FeedbackSubmittedPayload(sentiment="positive", rating=5),
        run_id=uuid4(),
        user_id=uuid4(),
    )
    events = [
        submitted,
        *[
            event(
                workspace,
                EventName.FEEDBACK_WITHDRAWN,
                FeedbackWithdrawnPayload(submission_event_id=submitted.event_id),
                day=day,
            )
            for day in (1, 2)
        ],
    ]
    if delivery == "concurrent":
        await asyncio.gather(*(ingestion.ingest(item) for item in events))
    else:
        for index in delivery:
            await ingestion.ingest(events[index])
    for item in events:
        assert not await ingestion.ingest(item)
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM feedback_projection"))).mappings().one()
        assert row["feedback_id"] == submitted.event_id
        assert row["sentiment"] == "positive"
        assert row["withdrawn_at"] == DAY + timedelta(days=1)
        assert (
            await conn.scalar(
                text("SELECT count(*) FROM feedback_projection WHERE withdrawn_at IS NULL")
            )
            == 0
        )

    independent = event(
        workspace,
        EventName.FEEDBACK_SUBMITTED,
        submitted.payload,
        run_id=submitted.run_id,
        user_id=submitted.user_id,
    )
    await ingestion.ingest(
        event(
            uuid4(),
            EventName.FEEDBACK_WITHDRAWN,
            FeedbackWithdrawnPayload(submission_event_id=independent.event_id),
            day=1,
        )
    )
    await ingestion.ingest(independent)
    async with transaction() as conn:
        assert (
            await conn.scalar(
                text("SELECT count(*) FROM feedback_projection WHERE withdrawn_at IS NULL")
            )
            == 1
        )


async def test_feedback_migration_recovers_acknowledged_withdrawals(analytics_db):
    workspace, transaction = analytics_db
    submissions = [
        event(
            workspace,
            EventName.FEEDBACK_SUBMITTED,
            FeedbackSubmittedPayload(sentiment="positive", rating=5),
            run_id=uuid4(),
        )
        for _ in range(2)
    ]
    withdrawals = [
        event(
            workspace,
            EventName.FEEDBACK_WITHDRAWN,
            FeedbackWithdrawnPayload(submission_event_id=submitted.event_id),
            day=1,
        )
        for submitted in submissions
    ]
    for item in withdrawals:
        await ingestion.ingest(item)
    await ingestion.ingest(submissions[0])
    async with transaction() as conn:
        # Recreate the old state: acknowledged withdrawals with no retained pending record.
        await conn.execute(text("UPDATE feedback_projection SET withdrawn_at = NULL"))
        await conn.execute(text("DROP TABLE feedback_withdrawal_projection"))
        schema = await conn.scalar(text("SELECT current_schema()"))
        migration = (
            Path(database.__file__).with_name("migrations") / "0003_feedback_withdrawals.sql"
        )
        raw = await conn.get_raw_connection()
        await raw.driver_connection.execute(migration.read_text().replace("open_swe", schema))
    await ingestion.ingest(submissions[1])
    for item in withdrawals:
        assert not await ingestion.ingest(item)
    async with transaction() as conn:
        rows = (
            (await conn.execute(text("SELECT withdrawn_at FROM feedback_projection")))
            .scalars()
            .all()
        )
        assert rows == [DAY + timedelta(days=1)] * 2


@pytest.mark.parametrize("terminal_first", [True, False])
async def test_run_start_preserves_preparation_link_in_either_delivery_order(
    analytics_db, terminal_first
):
    workspace, transaction = analytics_db
    run_id, preparation_id = uuid4(), uuid4()
    started = event(
        workspace,
        EventName.RUN_STARTED,
        RunStartedPayload(model_attribution_quality="unavailable"),
        run_id=run_id,
        preparation_run_id=preparation_id,
    )
    completed = event(
        workspace, EventName.RUN_COMPLETED, RunCompletedPayload(), day=1, run_id=run_id
    )
    for item in (completed, started) if terminal_first else (started, completed):
        await ingestion.ingest(item)
    async with transaction() as conn:
        await conn.execute(text("DELETE FROM events"))
        row = (await conn.execute(text("SELECT * FROM run_projection"))).mappings().one()
        assert row["preparation_run_id"] == preparation_id
        assert row["started_at"] == DAY
        assert row["terminal_at"] == DAY + timedelta(days=1)
