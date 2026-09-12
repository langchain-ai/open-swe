"""Usage projections preserve quantitative observations through retries and reordering."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from agent.analytics import directory, emitter, identity, ingestion
from agent.analytics.events import EventName, RunCanceledPayload, RunFailedPayload
from tests.analytics.helpers import DAY, event


@pytest.fixture(autouse=True)
async def usage_storage(analytics_db, monkeypatch):
    _, transaction = analytics_db
    monkeypatch.setenv("POSTGRES_URI", "postgresql://localhost/analytics_test")
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


async def test_directory_upgrades_email_and_login_captures_to_github_id(analytics_db):
    _, transaction = analytics_db
    email_person = await directory.resolve_person(email=" Person@Example.com ")
    login_person = await directory.resolve_person(github_login="old-login")
    canonical = await directory.resolve_person(
        immutable_person_key=123,
        github_login="old-login",
        email="person@example.com",
        display_name="Person",
    )
    assert canonical == identity.opaque_person("github", 123)
    assert await directory.resolve_person(github_login="OLD-LOGIN") == canonical
    assert await directory.resolve_person(email="PERSON@example.com") == canonical
    assert (
        await directory.resolve_person(immutable_person_key=123, github_login="new-login")
        == canonical
    )
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM identity_directory"))).mappings().one()
        assert row["github_login"] == "new-login"
        assert row["email"] == "person@example.com"
        aliases = dict(
            (
                await conn.execute(text("SELECT alias_person_id, person_id FROM identity_aliases"))
            ).all()
        )
        assert aliases[email_person] == canonical
        assert aliases[login_person] == canonical


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


async def test_strict_capture_propagates_persistence_failure(monkeypatch):
    async def unavailable(event):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(emitter, "enqueue", unavailable)
    with pytest.raises(RuntimeError, match="storage unavailable"):
        await emitter.pr_observed(owner="org", repo="repo", number=1, occurred_at=DAY)


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


async def test_sparse_pr_observations_order_each_non_null_metric_independently(analytics_db):
    _, transaction = analytics_db
    common = {"owner": "org", "repo": "repo", "number": 1}
    await emitter.pr_observed(**common, occurred_at=DAY + timedelta(days=3), changed_files=5)
    await emitter.pr_observed(**common, occurred_at=DAY, additions=10, deletions=1, changed_files=2)
    await emitter.pr_observed(**common, occurred_at=DAY + timedelta(days=2), additions=30)
    await emitter.pr_observed(
        **common, occurred_at=DAY + timedelta(days=1), additions=20, deletions=4
    )
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM pr_usage_projection"))).mappings().one()
        assert (row["additions"], row["deletions"], row["changed_files"]) == (30, 4, 5)


async def test_same_github_revision_can_supply_complementary_pr_measurements(analytics_db):
    _, transaction = analytics_db
    common = {"owner": "org", "repo": "repo", "number": 1, "occurred_at": DAY}
    assert await emitter.pr_observed(**common, changed_files=2)
    assert await emitter.pr_observed(**common, additions=30, deletions=4)
    assert not await emitter.pr_observed(**common, additions=30, deletions=4)
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM pr_usage_projection"))).mappings().one()
        assert (row["additions"], row["deletions"], row["changed_files"]) == (30, 4, 2)


@pytest.mark.parametrize("attribute", ["github_login", "email"])
@pytest.mark.parametrize("provisional_first", [False, True])
async def test_reassigned_handle_does_not_resolve_through_historical_alias(
    analytics_db, attribute, provisional_first
):
    _, transaction = analytics_db
    old = "old@example.com" if attribute == "email" else "old-login"
    new = "new@example.com" if attribute == "email" else "new-login"
    historical = await directory.resolve_person(**{attribute: old}) if provisional_first else None
    previous_owner = await directory.resolve_person(immutable_person_key=123, **{attribute: old})
    await directory.resolve_person(immutable_person_key=123, **{attribute: new})
    new_owner = await directory.resolve_person(**{attribute: old})
    assert new_owner != previous_owner
    assert new_owner != historical
    assert await directory.resolve_person(**{attribute: old}) == new_owner
    assert await directory.resolve_person(**{attribute: new}) == previous_owner
    canonical = await directory.resolve_person(immutable_person_key=456, **{attribute: old})
    assert canonical != previous_owner
    async with transaction() as conn:
        owners = {
            row["person_id"]: row
            for row in (await conn.execute(text("SELECT * FROM identity_directory"))).mappings()
        }
        assert owners[previous_owner][attribute] == new
        assert owners[canonical][attribute] == old
        aliases = dict(
            (
                await conn.execute(text("SELECT alias_person_id, person_id FROM identity_aliases"))
            ).all()
        )
        assert aliases[new_owner] == canonical
        if historical:
            assert aliases[historical] == previous_owner


@pytest.mark.parametrize("replacement_team", [None, "new-team"])
async def test_identity_upgrade_preserves_team_unless_explicitly_replaced(
    analytics_db, replacement_team
):
    _, transaction = analytics_db
    provisional = await directory.resolve_person(github_login="person", team_key="team")
    canonical = await directory.resolve_person(
        immutable_person_key=123, github_login="person", team_key=replacement_team
    )
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM identity_directory"))).mappings().one()
        assert row["person_id"] == canonical
        assert row["team_id"] == identity.opaque_id("team", replacement_team or "team")
        assert (
            await conn.scalar(
                text("SELECT person_id FROM identity_aliases WHERE alias_person_id = :provisional"),
                {"provisional": provisional},
            )
            == canonical
        )
