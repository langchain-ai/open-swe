"""Usage projections preserve quantitative observations through retries and reordering."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from openswe.analytics import directory, emitter, identity, ingestion
from openswe.analytics.events import EventName, RunCanceledPayload, RunFailedPayload
from tests.analytics.helpers import DAY, event


@pytest.fixture(autouse=True)
async def usage_storage(analytics_db, monkeypatch):
    _, transaction = analytics_db
    monkeypatch.setattr(ingestion, "transaction", transaction)
    monkeypatch.setattr(directory, "transaction", transaction)
    monkeypatch.setattr(emitter, "enqueue", ingestion.ingest)


async def test_pr_stats_reject_stale_delivery_and_keep_independent_owner(analytics_db):
    _, transaction = analytics_db
    person = uuid4()
    common = {"owner": "Org", "repo": "Repo", "number": 12}
    assert await emitter.pr_observed(
        **common,
        occurred_at=DAY + timedelta(days=3),
        source_version=3,
        additions=40,
        deletions=8,
        changed_files=4,
    )
    assert await emitter.pr_observed(
        **common,
        occurred_at=DAY,
        source_version=1,
        user_id=person,
        additions=10,
        deletions=2,
        changed_files=1,
    )
    assert not await emitter.pr_observed(
        **common,
        occurred_at=DAY,
        source_version=1,
        user_id=person,
        additions=10,
        deletions=2,
        changed_files=1,
    )
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM pr_usage_projection"))).mappings().one()
        assert (row["user_id"], row["additions"], row["deletions"], row["changed_files"]) == (
            person,
            40,
            8,
            4,
        )
        assert await conn.scalar(text("SELECT count(*) FROM pr_projection")) == 0


async def test_findings_keep_first_milestones_and_latest_observation(analytics_db):
    _, transaction = analytics_db
    common = {
        "thread_key": "thread",
        "finding_key": "finding",
        "owner": "org",
        "repo": "repo",
        "number": 1,
        "head_sha": "new",
        "first_seen_sha": "original",
        "category": "logic",
    }
    # A reopened observation arrives before the first recording and first resolution.
    await emitter.finding_observed(
        **common,
        observed_at=DAY + timedelta(days=3),
        state="open",
        severity="medium",
        human_replies=3,
        resolved_sha=None,
    )
    await emitter.finding_observed(
        **common,
        observed_at=DAY,
        recorded_at=DAY,
        state="open",
        severity="high",
        human_replies=0,
        resolved_sha=None,
    )
    await emitter.finding_observed(
        **common,
        observed_at=DAY + timedelta(days=2),
        surfaced_at=DAY + timedelta(days=1),
        state="resolved",
        severity="high",
        human_replies=2,
        resolved_sha="first-fix",
    )
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM finding_usage_projection"))).mappings().one()
        assert row["recorded_at"] == DAY
        assert row["surfaced_at"] == DAY + timedelta(days=1)
        assert row["resolved_at"] == DAY + timedelta(days=2)
        assert row["current_state"] == "open"
        assert row["severity"] == "medium"
        assert row["human_replies"] == 3
        assert row["resolved_revision_id"] == identity.opaque_id("revision", "first-fix")
        await conn.execute(text("DELETE FROM events"))
    assert await emitter.finding_observed(
        **common,
        observed_at=DAY + timedelta(days=4),
        state="resolved",
        severity="medium",
        human_replies=4,
        resolved_sha="second-fix",
    )
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM finding_usage_projection"))).mappings().one()
        assert row["current_state"] == "resolved"
        assert row["resolved_revision_id"] == identity.opaque_id("revision", "first-fix")
        assert row["human_replies"] == 4


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        (EventName.RUN_FAILED, RunFailedPayload(failure_class="error", total_tokens=9)),
        (EventName.RUN_CANCELED, RunCanceledPayload(cancellation_source="user", total_tokens=9)),
    ],
)
async def test_unsuccessful_invocations_preserve_token_accounting(analytics_db, name, payload):
    workspace, transaction = analytics_db
    await ingestion.ingest(event(workspace, name, payload, run_id=uuid4()))
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT total_tokens FROM run_projection")) == 9


async def test_observation_lifecycle_counts_state_changes_and_survives_retention(analytics_db):
    _, transaction = analytics_db
    common = {
        "thread_key": "thread",
        "finding_key": "finding",
        "owner": "org",
        "repo": "repo",
        "number": 1,
        "head_sha": "head",
        "first_seen_sha": "original",
        "category": "logic",
        "severity": "high",
        "human_replies": 0,
        "recorded_at": DAY,
        "surfaced_at": DAY,
    }
    for day, state in enumerate(["open", "resolved", "resolved", "open", "open"]):
        await emitter.finding_observed(
            **common,
            observed_at=DAY + timedelta(days=day),
            state=state,
            resolved_sha="fix" if state == "resolved" else None,
        )
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM finding_projection"))).mappings().one()
        assert row["current_state"] == "open"
        assert row["reopened_count"] == 1
        assert row["resolved_at"] == DAY + timedelta(days=1)
        await conn.execute(text("DELETE FROM events"))
    for day, state in [(5, "resolved"), (6, "resolved"), (7, "open"), (8, "open")]:
        if day == 6:
            async with transaction() as conn:
                await conn.execute(text("DELETE FROM events"))
        await emitter.finding_observed(
            **common,
            observed_at=DAY + timedelta(days=day),
            state=state,
            resolved_sha="second-fix" if state == "resolved" else None,
        )
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM finding_projection"))).mappings().one()
        assert row["current_state"] == "open"
        assert row["reopened_count"] == 2
        assert row["resolved_at"] == DAY + timedelta(days=5)


async def test_same_github_revision_can_supply_complementary_pr_measurements(analytics_db):
    _, transaction = analytics_db
    common = {"owner": "org", "repo": "repo", "number": 1, "occurred_at": DAY}
    assert await emitter.pr_observed(**common, changed_files=2)
    assert await emitter.pr_observed(**common, additions=30, deletions=4)
    assert not await emitter.pr_observed(**common, additions=30, deletions=4)
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM pr_usage_projection"))).mappings().one()
        assert (row["additions"], row["deletions"], row["changed_files"]) == (30, 4, 2)


async def test_legacy_unsourced_name_blocks_slack_and_yields_to_github(analytics_db):
    _, transaction = analytics_db
    person = await directory.resolve_person(immutable_person_key=123, display_name="Legacy Name")
    assert await directory.resolve_person(immutable_person_key=123, display_name="  ") == person
    assert (
        await directory.resolve_person(
            immutable_person_key=123, display_name="Grace (Slack)", display_name_source="slack"
        )
        == person
    )
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM identity_directory"))).mappings().one()
        assert row["display_name"] == "Legacy Name"
        assert row["display_name_source"] is None
    assert await directory.resolve_person(immutable_person_key=123, display_name=None) == person
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM identity_directory"))).mappings().one()
        assert row["display_name"] == "Legacy Name"
        assert row["display_name_source"] is None
    assert (
        await directory.resolve_person(
            immutable_person_key=123, display_name="Grace Hopper", display_name_source="github"
        )
        == person
    )
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM identity_directory"))).mappings().one()
        assert row["display_name"] == "Grace Hopper"
        assert row["display_name_source"] == "github"


async def test_reused_login_never_merges_durable_identities(analytics_db):
    _, transaction = analytics_db
    owner = await directory.resolve_person(
        immutable_person_key=123, github_login="handle", display_name="Owner"
    )
    successor = await directory.resolve_person(
        immutable_person_key=456, github_login="handle", display_name="Successor"
    )
    assert successor is not None
    assert successor != owner
    async with transaction() as conn:
        rows = {
            row["person_id"]: row
            for row in (await conn.execute(text("SELECT * FROM identity_directory"))).mappings()
        }
        assert rows[successor]["github_login"] == "handle"
        assert rows[owner]["github_login"] is None
        assert rows[owner]["identity_kind"] == "immutable"
        assert rows[successor]["identity_kind"] == "immutable"
