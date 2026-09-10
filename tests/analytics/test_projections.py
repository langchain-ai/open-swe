"""Database regressions; set TEST_ANALYTICS_POSTGRES_URI to a disposable PostgreSQL DB."""

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from agent.analytics import database, emitter, ingestion, queries, summaries
from agent.analytics.events import (
    EventName,
    FindingStatePayload,
    FindingSurfacedPayload,
    PROpenedPayload,
    PRStatePayload,
    ReviewPublishedPayload,
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
    migration = (
        (Path(database.__file__).with_name("migrations") / "0001_analytics.sql")
        .read_text()
        .replace("open_swe_analytics", schema)
    )
    async with engine.begin() as conn:
        await database._run_script(conn, migration)

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
