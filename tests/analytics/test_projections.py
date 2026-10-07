"""Durable projections reconcile duplicate and out-of-order events."""

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from openswe.analytics import identity, ingestion
from openswe.analytics.events import (
    EventName,
    FeedbackSubmittedPayload,
    FeedbackWithdrawnPayload,
    FindingStatePayload,
    FindingSurfacedPayload,
    PRObservedPayload,
    PROpenedPayload,
    PRRunLinkedPayload,
    PRStatePayload,
    RunCostRecordedPayload,
    RunStartedPayload,
)
from openswe.database import postgres
from tests.analytics.helpers import DAY, event


@pytest.fixture(autouse=True)
async def projection_storage(analytics_db, monkeypatch):
    _, transaction = analytics_db
    monkeypatch.setattr(ingestion, "transaction", transaction)


async def test_directory_preserves_immutable_identity_after_login_change(analytics_db, monkeypatch):
    from openswe.analytics import directory

    workspace, transaction = analytics_db
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


async def test_pr_attribution_repair_is_conservative_and_idempotent(analytics_db):
    workspace, transaction = analytics_db
    configured_model, existing_model = uuid4(), uuid4()
    recoverable_run, later_run, ambiguous_run = uuid4(), uuid4(), uuid4()
    recoverable_pr, attributed_pr, missing_pr, ambiguous_pr = (uuid4() for _ in range(4))
    for run_id, model_id in (
        (recoverable_run, configured_model),
        (later_run, existing_model),
        (ambiguous_run, None),
    ):
        await ingestion.ingest(
            event(
                workspace,
                EventName.RUN_STARTED,
                RunStartedPayload(
                    configured_model_id=model_id,
                    model_attribution_quality="configured" if model_id else "unavailable",
                ),
                run_id=run_id,
            )
        )
    for pr_id, opening_run_id, model_id, quality in (
        (recoverable_pr, recoverable_run, None, "unavailable"),
        (attributed_pr, recoverable_run, existing_model, "configured"),
        (missing_pr, None, None, "unavailable"),
        (ambiguous_pr, ambiguous_run, None, "unavailable"),
    ):
        await ingestion.ingest(
            event(
                workspace,
                EventName.PR_OPENED,
                PROpenedPayload(
                    opening_run_id=opening_run_id,
                    originating_model_id=model_id,
                    model_attribution_quality=quality,
                ),
                pr_id=pr_id,
                repository_id=uuid4(),
            )
        )
    async with transaction() as conn:
        await ingestion._repair_pr_attribution(conn, workspace_id=workspace)
        await ingestion._repair_pr_attribution(conn, workspace_id=workspace)
        await conn.execute(
            text(
                "INSERT INTO pr_run_link_projection "
                "(workspace_id, pr_id, run_id, link_role, linked_at) "
                "VALUES (:workspace, :pr, :run, 'follow_up', :linked_at)"
            ),
            {"workspace": workspace, "pr": missing_pr, "run": later_run, "linked_at": DAY},
        )
    async with transaction() as conn:
        rows = {
            row["pr_id"]: row
            for row in (
                await conn.execute(
                    text(
                        "SELECT pr_id, originating_model_id, model_attribution_quality "
                        "FROM pr_projection"
                    )
                )
            ).mappings()
        }
        assert rows[recoverable_pr]["originating_model_id"] == configured_model
        assert rows[attributed_pr]["originating_model_id"] == existing_model
        assert rows[missing_pr]["originating_model_id"] is None
        assert rows[ambiguous_pr]["originating_model_id"] is None


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


@pytest.mark.parametrize("order", [(0, 1, 2), (2, 1, 0), "concurrent"])
@pytest.mark.parametrize("has_model", [False, True])
async def test_false_opening_is_rejected_across_delivery_and_replay(analytics_db, order, has_model):
    workspace, transaction = analytics_db
    pr_id, run_id, model_id = uuid4(), uuid4(), uuid4()
    events = [
        event(
            workspace,
            EventName.PR_OPENED,
            PROpenedPayload(
                opening_run_id=run_id,
                originating_model_id=model_id if has_model else None,
                model_attribution_quality="configured" if has_model else "unavailable",
            ),
            pr_id=pr_id,
            repository_id=uuid4(),
        ),
        event(
            workspace,
            EventName.RUN_STARTED,
            RunStartedPayload(configured_model_id=model_id, model_attribution_quality="configured"),
            run_id=run_id,
            day=12,
        ),
        event(
            workspace,
            EventName.PR_RUN_LINKED,
            PRRunLinkedPayload(link_role="opening"),
            pr_id=pr_id,
            run_id=run_id,
            day=12,
        ),
    ]
    follow_up = event(
        workspace,
        EventName.PR_RUN_LINKED,
        PRRunLinkedPayload(link_role="follow_up"),
        pr_id=pr_id,
        run_id=run_id,
        day=12,
    )
    merged = event(workspace, EventName.PR_MERGED, PRStatePayload(), pr_id=pr_id, day=13)
    await ingestion.ingest(merged)
    await ingestion.ingest(
        event(workspace, EventName.PR_OBSERVED, PRObservedPayload(additions=42), pr_id=pr_id)
    )
    await ingestion.ingest(follow_up)
    if order == "concurrent":
        await asyncio.gather(*(ingestion.ingest(item) for item in events))
        order = (0, 1, 2)
    else:
        for index in order:
            await ingestion.ingest(events[index])
    async with transaction() as conn:
        for _ in range(2):
            for index in reversed(order):
                await ingestion._project(conn, events[index])
            await ingestion._repair_pr_attribution(conn, workspace_id=workspace)
        row = (await conn.execute(text("SELECT * FROM pr_projection"))).mappings().one()
        assert row["opening_run_id"] is None
        assert row["originating_model_id"] is None
        assert row["model_attribution_quality"] == "unavailable"
        assert row["current_state"] == "merged"
        assert row["latest_transition_at"] == DAY + timedelta(days=13)
        links = (
            (await conn.execute(text("SELECT run_id, link_role FROM pr_run_link_projection")))
            .tuples()
            .all()
        )
        assert links == [(run_id, "follow_up")]
        assert await conn.scalar(text("SELECT additions FROM pr_usage_projection")) == 42
        assert await conn.scalar(text("SELECT count(*) FROM events")) == 6
    for item in events:
        assert not await ingestion.ingest(item)


@pytest.mark.parametrize(
    "guard",
    [
        "contradicted",
        "boundary",
        "within",
        "earlier",
        "missing_start",
        "missing_pr",
        "missing_run",
        "other_run",
        "other_pr",
        "different_opener",
    ],
)
@pytest.mark.parametrize("outcome", [EventName.PR_MERGED])
async def test_historical_opener_migration_is_scoped_and_idempotent(
    analytics_db, monkeypatch, guard, outcome
):
    from unittest.mock import AsyncMock

    _, transaction = analytics_db
    workspace = UUID("7849c27e-81ef-4651-8982-719c84d95e7d")
    pr_id = UUID("aba01c5a-66a4-5b57-97f2-1db4d93de929")
    run_id = UUID("455db9e6-ff8d-5aea-baed-72b11b609348")
    original_run = UUID("95562c2a-c095-4f15-8d1b-35cab031c991")
    other_workspace, model_id, review_run = uuid4(), uuid4(), uuid4()
    if guard == "other_run":
        run_id = original_run
    if guard == "other_pr":
        pr_id = uuid4()
    with monkeypatch.context() as patch:
        patch.setattr(ingestion, "_reject_false_openers", AsyncMock())
        for scope in (workspace, other_workspace):
            for item in (
                event(
                    scope,
                    EventName.RUN_STARTED,
                    RunStartedPayload(
                        configured_model_id=model_id, model_attribution_quality="configured"
                    ),
                    run_id=run_id,
                    thread_id=uuid4(),
                    day=12,
                ),
                event(
                    scope,
                    EventName.PR_OPENED,
                    PROpenedPayload(
                        opening_run_id=original_run if guard == "different_opener" else run_id,
                        originating_model_id=model_id,
                        model_attribution_quality="configured",
                    ),
                    pr_id=pr_id,
                    repository_id=uuid4(),
                ),
                event(scope, outcome, PRStatePayload(), pr_id=pr_id, day=13),
                event(scope, EventName.PR_OBSERVED, PRObservedPayload(additions=42), pr_id=pr_id),
                *(
                    event(
                        scope,
                        EventName.PR_RUN_LINKED,
                        PRRunLinkedPayload(link_role=role),
                        pr_id=pr_id,
                        run_id=linked_run,
                    )
                    for role, linked_run in (
                        ("opening", run_id),
                        ("follow_up", run_id),
                        ("review", review_run),
                        ("opening", original_run),
                    )
                ),
            ):
                await ingestion.ingest(item)
    async with transaction() as conn:
        if guard in {"boundary", "within", "earlier", "missing_start"}:
            skew = {
                "boundary": timedelta(hours=24),
                "within": timedelta(minutes=5),
                "earlier": timedelta(days=-1),
                "missing_start": None,
            }[guard]
            await conn.execute(
                text("UPDATE run_projection SET started_at = :started"),
                {"started": DAY + skew if skew is not None else None},
            )
        elif guard == "missing_pr":
            await conn.execute(text("DELETE FROM pr_projection"))
        elif guard == "missing_run":
            await conn.execute(text("DELETE FROM run_projection"))
        preserved_tables = (
            "events",
            "run_projection",
            "pr_usage_projection",
            "additive_event_projection",
        )
        before = {
            table: (await conn.execute(text(f"SELECT * FROM {table}"))).all()
            for table in preserved_tables
        }
        prs_before = (
            (await conn.execute(text("SELECT * FROM pr_projection ORDER BY workspace_id")))
            .mappings()
            .all()
        )
        links_before = set((await conn.execute(text("SELECT * FROM pr_run_link_projection"))).all())
        await conn.run_sync(postgres.execute_revision, postgres.load_migrations(), "0017")
        prs_after = (
            (await conn.execute(text("SELECT * FROM pr_projection ORDER BY workspace_id")))
            .mappings()
            .all()
        )
        links_after = set((await conn.execute(text("SELECT * FROM pr_run_link_projection"))).all())
        for old, new in zip(prs_before, prs_after, strict=True):
            expected = dict(old)
            if old["workspace_id"] == workspace and guard == "contradicted":
                expected.update(
                    opening_run_id=None,
                    originating_model_id=None,
                    model_attribution_quality="unavailable",
                    updated_at=new["updated_at"],
                )
            assert dict(new) == expected
        assert links_after == {
            row
            for row in links_before
            if not (
                guard in {"contradicted", "different_opener"}
                and row.workspace_id == workspace
                and row.run_id == run_id
                and row.link_role == "opening"
            )
        }
        await conn.run_sync(postgres.execute_revision, postgres.load_migrations(), "0017")
        assert (
            await conn.execute(text("SELECT * FROM pr_projection ORDER BY workspace_id"))
        ).mappings().all() == prs_after
        assert (
            set((await conn.execute(text("SELECT * FROM pr_run_link_projection"))).all())
            == links_after
        )
        for table in preserved_tables:
            assert (await conn.execute(text(f"SELECT * FROM {table}"))).all() == before[table]
