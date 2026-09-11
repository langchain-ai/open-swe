"""Database regressions; set TEST_ANALYTICS_POSTGRES_URI to a disposable PostgreSQL DB."""

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from itertools import permutations
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from agent.analytics import database, emitter, ingestion, queries, summaries
from agent.analytics.events import (
    EventName,
    FeedbackSubmittedPayload,
    FeedbackWithdrawnPayload,
    FindingStatePayload,
    FindingSurfacedPayload,
    PROpenedPayload,
    PRStatePayload,
    ReviewPublishedPayload,
    RunCompletedPayload,
    RunCostRecordedPayload,
    RunStartedPayload,
    make_event,
)

DAY = datetime(2026, 9, 7, tzinfo=UTC)


@pytest.fixture
async def analytics_db(monkeypatch):
    uri = os.environ.get("TEST_ANALYTICS_POSTGRES_URI")
    if not uri:
        pytest.skip("TEST_ANALYTICS_POSTGRES_URI is required for PostgreSQL regressions")
    engine = create_async_engine(uri)
    schema = f"analytics_test_{uuid4().hex}"
    workspace = uuid4()
    monkeypatch.setenv("ANALYTICS_WORKSPACE_ID", str(workspace))
    monkeypatch.setenv("ANALYTICS_EPOCH", DAY.isoformat())
    monkeypatch.setenv("ANALYTICS_SUMMARY_VERSION", "1")
    async with engine.begin() as conn:
        for path in sorted(Path(database.__file__).with_name("migrations").glob("*.sql")):
            await database._run_script(conn, path.read_text().replace("open_swe_analytics", schema))

    @asynccontextmanager
    async def transaction():
        async with engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL search_path TO {schema}, public"))
            await conn.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            yield conn

    monkeypatch.setattr(ingestion, "transaction", transaction)
    monkeypatch.setattr(summaries, "transaction", transaction)
    monkeypatch.setattr(queries, "connection", transaction)
    try:
        yield workspace, transaction
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await engine.dispose()


def event(workspace, name, payload, *, day=0, **identifiers):
    return make_event(
        workspace_id=workspace,
        event_name=name,
        producer="test",
        producer_event_id=str(uuid4()),
        occurred_at=DAY + timedelta(days=day),
        environment="test",
        payload=payload,
        **identifiers,
    )


async def flush_summaries():
    while await summaries.recompute_dirty_partitions(limit=100):
        pass


@pytest.mark.parametrize("version", [None, 2])
@pytest.mark.parametrize(
    ("outcome_name", "state"),
    [
        (EventName.PR_MERGED, "merged"),
        (EventName.PR_CLOSED_WITHOUT_MERGE, "closed_without_merge"),
        (EventName.PR_REOPENED, "open"),
    ],
)
@pytest.mark.parametrize("delivery", ["outcome_first", "opening_first", "concurrent"])
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
    await flush_summaries()
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM pr_projection"))).mappings().one()
        assert row["current_state"] == state
        assert row["opened_at"] == DAY
        assert row["outcome_at"] == (None if state == "open" else DAY + timedelta(days=1))
        counters = await conn.scalar(
            text(
                "SELECT counters FROM daily_summaries WHERE family = 'pr_open_cohort' AND partition_date = :day"
            ),
            {"day": DAY.date()},
        )
        assert counters == {state: 1}


@pytest.mark.parametrize("versioned", [False, True])
@pytest.mark.parametrize("surface_first", [False, True])
@pytest.mark.parametrize(
    ("outcome_name", "state", "timestamp"),
    [
        (EventName.FINDING_RESOLVED, "resolved", "resolved_at"),
        (EventName.FINDING_DISMISSED, "dismissed", "dismissed_at"),
        (EventName.FINDING_REOPENED, "open", None),
    ],
)
async def test_finding_outcome_survives_delivery_order(
    analytics_db, versioned, surface_first, outcome_name, state, timestamp
):
    workspace, transaction = analytics_db
    finding_id, review_id, pr_id = uuid4(), uuid4(), uuid4()
    surfaced = event(
        workspace,
        EventName.FINDING_SURFACED,
        FindingSurfacedPayload(severity="high", category="correctness"),
        finding_id=finding_id,
        review_id=review_id,
        pr_id=pr_id,
    )
    outcome = event(
        workspace,
        outcome_name,
        FindingStatePayload(),
        day=1,
        finding_id=finding_id,
        source_version=2 if versioned else None,
    )
    for item in [surfaced, outcome] if surface_first else [outcome, surfaced]:
        assert await ingestion.ingest(item)
    assert not await ingestion.ingest(outcome)
    # A second surfacing must not count the same reopen again.
    await ingestion.ingest(
        event(
            workspace,
            EventName.FINDING_SURFACED,
            surfaced.payload,
            day=2,
            finding_id=finding_id,
            review_id=review_id,
            pr_id=pr_id,
        )
    )
    await flush_summaries()
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM finding_projection"))).mappings().one()
        assert row["current_state"] == state
        assert row["surfaced_at"] == DAY
        assert row["review_id"] == review_id
        assert row["reopened_count"] == (1 if outcome_name == EventName.FINDING_REOPENED else 0)
        if timestamp:
            assert row[timestamp] == DAY + timedelta(days=1)
        counters = await conn.scalar(
            text(
                "SELECT counters FROM daily_summaries WHERE family = 'finding_surfaced_cohort' AND partition_date = :day"
            ),
            {"day": DAY.date()},
        )
        assert counters == {state: 1}


async def test_finding_reconciles_multiple_pending_transitions(analytics_db):
    workspace, transaction = analytics_db
    finding_id = uuid4()
    for day, name in [
        (3, EventName.FINDING_DISMISSED),
        (1, EventName.FINDING_RESOLVED),
        (2, EventName.FINDING_REOPENED),
    ]:
        await ingestion.ingest(
            event(
                workspace,
                name,
                FindingStatePayload(),
                day=day,
                finding_id=finding_id,
                source_version=day,
            )
        )
    await ingestion.ingest(
        event(
            workspace,
            EventName.FINDING_SURFACED,
            FindingSurfacedPayload(severity="high", category="correctness"),
            finding_id=finding_id,
        )
    )
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM finding_projection"))).mappings().one()
        assert row["current_state"] == "dismissed"
        assert row["resolved_at"] == DAY + timedelta(days=1)
        assert row["dismissed_at"] == DAY + timedelta(days=3)
        assert row["reopened_count"] == 1


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
        async with transaction() as conn:
            assert (
                await conn.scalar(
                    text(
                        "SELECT count(*) FROM dirty_summary_partitions WHERE family = 'cost_completeness' "
                        "AND partition_date = :day"
                    ),
                    {"day": DAY.date()},
                )
                == 1
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


async def test_reviewer_counts_distinct_prs_and_zero_finding_reviews(analytics_db):
    workspace, _ = analytics_db
    pr_with_findings, pr_without_findings = uuid4(), uuid4()
    for pr_id, count in [(pr_with_findings, 2), (pr_with_findings, 1), (pr_without_findings, 0)]:
        await ingestion.ingest(
            event(
                workspace,
                EventName.REVIEW_PUBLISHED,
                ReviewPublishedPayload(finding_count=count),
                day=1,
                pr_id=pr_id,
                review_id=uuid4(),
            )
        )
    for _ in range(3):
        await ingestion.ingest(
            event(
                workspace,
                EventName.FINDING_SURFACED,
                FindingSurfacedPayload(severity="high", category="correctness"),
                day=1,
                pr_id=pr_with_findings,
                finding_id=uuid4(),
            )
        )
    for other_workspace, day in [(workspace, 0), (uuid4(), 1)]:
        await ingestion.ingest(
            event(
                other_workspace,
                EventName.REVIEW_PUBLISHED,
                ReviewPublishedPayload(finding_count=0),
                day=day,
                pr_id=uuid4(),
                review_id=uuid4(),
            )
        )
        await ingestion.ingest(
            event(
                other_workspace,
                EventName.FINDING_SURFACED,
                FindingSurfacedPayload(severity="low", category="correctness"),
                day=day,
                pr_id=uuid4(),
                finding_id=uuid4(),
            )
        )
    stats = await queries.reviewer_stats(DAY + timedelta(days=1))
    assert stats["reviewed_prs"] == 2
    assert stats["prs_with_findings"] == 1
    assert stats["surfaced_findings"] == 3
    assert stats["unresolved_surfaced_findings"] == 3


async def test_reviewer_counts_zero_finding_only_review(analytics_db):
    workspace, _ = analytics_db
    await ingestion.ingest(
        event(
            workspace,
            EventName.REVIEW_PUBLISHED,
            ReviewPublishedPayload(finding_count=0),
            pr_id=uuid4(),
            review_id=uuid4(),
        )
    )
    stats = await queries.reviewer_stats(DAY)
    assert stats["reviewed_prs"] == 1
    assert stats["prs_with_findings"] == 0
    assert stats["surfaced_findings"] == 0


async def test_emitted_finding_links_to_published_review(analytics_db, monkeypatch):
    _, transaction = analytics_db

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return DAY

    monkeypatch.setattr(emitter, "datetime", FixedDatetime)
    monkeypatch.setenv("ANALYTICS_POSTGRES_URI", "postgresql://localhost/analytics_test")
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


async def test_leaderboard_resolves_immutable_identity_after_login_change(
    analytics_db, monkeypatch
):
    from agent.analytics import directory

    workspace, transaction = analytics_db
    monkeypatch.setenv("ANALYTICS_POSTGRES_URI", "postgresql://localhost/analytics_test")
    monkeypatch.setattr(directory, "transaction", transaction)
    current_person = emitter.opaque_person("github", 123)
    other_person = emitter.opaque_person("github", 456)
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
    result = await queries.usage_leaderboard(
        period="all", limit=1, current_login="NEW-LOGIN", admin=False
    )
    assert result["current_user_rank"] == 2
    assert len(result["rows"]) == 2
    assert result["rows"][0]["user"] == {
        "name": "Open SWE user",
        "github_login": None,
        "email": None,
    }
    assert result["rows"][1]["user"] == {
        "name": "new-login",
        "github_login": "new-login",
        "email": "self@example.com",
    }
    assert all("is_current" not in row for row in result["rows"])
    for login in ("old-login", "unknown", None):
        result = await queries.usage_leaderboard(
            period="all", limit=1, current_login=login, admin=False
        )
        assert result["current_user_rank"] is None
        assert len(result["rows"]) == 1
        assert result["rows"][0]["user"]["github_login"] is None

    monkeypatch.setenv("ANALYTICS_WORKSPACE_ID", str(uuid4()))
    result = await queries.usage_leaderboard(
        period="all", limit=1, current_login="new-login", admin=False
    )
    assert result["current_user_rank"] is None
    assert result["rows"] == []


@pytest.mark.parametrize("delivery", list(permutations(range(3))))
@pytest.mark.parametrize("versioned", [False, True])
async def test_pr_reopening_rejects_delayed_close(analytics_db, delivery, versioned):
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
    for index in delivery:
        assert await ingestion.ingest(events[index])
    for item in events:
        assert not await ingestion.ingest(item)
    await flush_summaries()
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM pr_projection"))).mappings().one()
        assert row["current_state"] == "open"
        assert row["outcome_at"] is None
        assert row["latest_transition_at"] == DAY + timedelta(days=2)
        assert await conn.scalar(
            text(
                "SELECT counters FROM daily_summaries WHERE family = 'pr_open_cohort' "
                "AND partition_date = :day"
            ),
            {"day": DAY.date()},
        ) == {"open": 1}

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
        await database._run_script(
            conn, migration.read_text().replace("open_swe_analytics", schema)
        )
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


@pytest.mark.parametrize("delivery", [*permutations(range(3)), "concurrent"])
@pytest.mark.parametrize("versioned", [False, True])
@pytest.mark.parametrize("surface_first", [False, True])
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
            await flush_summaries()
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
    await flush_summaries()
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM finding_projection"))).mappings().one()
        assert row["current_state"] == "dismissed"
        assert row["resolved_at"] == DAY + timedelta(days=1)
        assert row["dismissed_at"] == DAY + timedelta(days=3)
        assert row["latest_occurred_at"] == DAY + timedelta(days=3)
        assert row["reopened_count"] == 1
        counts = await conn.scalar(
            text(
                "SELECT histogram_counts FROM daily_summaries WHERE family = 'latency_histogram' "
                "AND partition_date = :day"
            ),
            {"day": DAY.date()},
        )
        assert sum(counts) == 1
    stats = await queries.reviewer_stats(DAY)
    assert stats["reopened_findings"] == 1
    assert stats["dismissed_findings"] == 1
    assert stats["unresolved_surfaced_findings"] == 0


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
        stats = await queries.reviewer_stats(DAY)
        assert stats["reopened_findings"] == index
        if index >= 2:
            async with transaction() as conn:
                await conn.execute(text("DELETE FROM events"))


async def test_additive_migration_preserves_expired_and_unsummarized_totals(analytics_db):
    workspace, transaction = analytics_db
    for day in (0, 0, 1):
        await ingestion.ingest(
            event(
                workspace,
                EventName.RUN_STARTED,
                RunStartedPayload(model_attribution_quality="unavailable"),
                run_id=uuid4(),
                day=day,
            )
        )
    await flush_summaries()
    async with transaction() as conn:
        await conn.execute(text("DELETE FROM events WHERE occurred_at = :day"), {"day": DAY})
    await ingestion.ingest(
        event(
            workspace,
            EventName.RUN_STARTED,
            RunStartedPayload(model_attribution_quality="unavailable"),
            run_id=uuid4(),
            day=1,
        )
    )
    async with transaction() as conn:
        await conn.execute(text("DROP TABLE additive_event_projection"))
        schema = await conn.scalar(text("SELECT current_schema()"))
        script = (
            (Path(database.__file__).with_name("migrations") / "0006_additive_event_projection.sql")
            .read_text()
            .replace("open_swe_analytics", schema)
        )
        await database._run_script(conn, script)
        await database._run_script(conn, script)
        rows = (
            await conn.execute(
                text(
                    "SELECT partition_date, event_count FROM additive_event_projection ORDER BY partition_date"
                )
            )
        ).all()
        assert rows == [(DAY.date(), 2), ((DAY + timedelta(days=1)).date(), 2)]


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
    await flush_summaries()
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM finding_projection"))).mappings().one()
        assert row["current_state"] == "resolved"
        assert row["source_version"] == latest_version
        assert row["latest_occurred_at"] == DAY + timedelta(days=10)
        assert await conn.scalar(
            text(
                "SELECT counters FROM daily_summaries WHERE family = 'finding_surfaced_cohort' "
                "AND partition_date = :day"
            ),
            {"day": DAY.date()},
        ) == {"resolved": 1}
    stats = await queries.reviewer_stats(DAY)
    assert stats["addressed_findings"] == 1
    assert stats["reopened_findings"] == 1


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
    "amounts, completeness",
    [
        ([None], "unavailable"),
        ([0], "complete"),
        ([None, 0], "partial"),
    ],
)
async def test_leaderboard_distinguishes_unknown_and_zero_cost(analytics_db, amounts, completeness):
    workspace, _ = analytics_db
    user_id = uuid4()
    for amount in amounts:
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
    result = await queries.usage_leaderboard(period="all", limit=10, current_login=None, admin=True)
    assert len(result["rows"]) == 1
    assert result["rows"][0]["cost_completeness"] == completeness
    assert result["rows"][0]["total_cost_usd"] == 0


@pytest.mark.parametrize("delivery", [*permutations(range(3)), "concurrent"])
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
        await database._run_script(
            conn, migration.read_text().replace("open_swe_analytics", schema)
        )
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


async def test_finding_history_baseline_survives_raw_event_retention(analytics_db):
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
            source_version=2,
        )
    )
    await ingestion.ingest(
        event(
            workspace,
            EventName.FINDING_REOPENED,
            FindingStatePayload(),
            finding_id=finding_id,
            day=2,
            source_version=3,
        )
    )
    async with transaction() as conn:
        # Simulate retention: drop the projection history along with its raw events.
        schema = await conn.scalar(text("SELECT current_schema()"))
        await database._run_script(
            conn,
            (Path(database.__file__).with_name("migrations") / "0004_finding_history_baseline.sql")
            .read_text()
            .replace("open_swe_analytics", schema),
        )
        await conn.execute(
            text("DELETE FROM events WHERE finding_id = :finding_id"), {"finding_id": finding_id}
        )
        await conn.execute(
            text(
                "UPDATE finding_projection SET resolved_at = NULL, dismissed_at = NULL, "
                "reopened_count = 0 WHERE finding_id = :finding_id"
            ),
            {"finding_id": finding_id},
        )
    # A much later transition must re-add history, not erase the pre-retention facts.
    await ingestion.ingest(
        event(
            workspace,
            EventName.FINDING_RESOLVED,
            FindingStatePayload(),
            finding_id=finding_id,
            day=40,
            source_version=5,
        )
    )
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM finding_projection"))).mappings().one()
        assert row["current_state"] == "resolved"
        assert row["resolved_at"] == DAY + timedelta(days=40)
        assert row["reopened_count"] == 1
    stats = await queries.reviewer_stats(DAY)
    assert stats["reopened_findings"] == 1
