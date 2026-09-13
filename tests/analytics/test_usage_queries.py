"""PostgreSQL reporting preserves usage cohorts, identities, and disclosure rules."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from agent.analytics import directory, queries
from agent.database import analytics as database

NOW = datetime(2026, 9, 11, tzinfo=UTC)


@pytest.fixture
async def usage_db(deployment_db, monkeypatch):
    await database.migrate()
    async with database.transaction() as conn:
        await conn.execute(
            text("UPDATE deployment_metadata SET reporting_cutover_at = :cutover"),
            {"cutover": NOW - timedelta(days=90)},
        )

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(queries, "datetime", FixedDatetime)
    return database.workspace_id()


async def insert(table, *, workspace_id=None, **values):
    values = {"workspace_id": workspace_id or database.workspace_id(), **values}
    async with database.transaction() as conn:
        await conn.execute(
            text(
                f"INSERT INTO {table} ({', '.join(values)}) "
                f"VALUES ({', '.join(':' + key for key in values)})"
            ),
            values,
        )


async def person(login=None, email=None, **kwargs):
    person_id = uuid4()
    await insert(
        "identity_directory",
        person_id=person_id,
        github_login=login,
        email=email,
        anonymize_after=NOW + timedelta(days=365),
        **kwargs,
    )
    return person_id


async def run(person_id, *, age=1, duration=None, model_id=None, tokens=None, **kwargs):
    run_id = uuid4()
    started = NOW - timedelta(days=age)
    await insert(
        "run_projection",
        run_id=run_id,
        user_id=person_id,
        started_at=started,
        terminal_at=started + timedelta(seconds=duration) if duration is not None else None,
        configured_model_id=model_id,
        total_tokens=tokens,
        **kwargs,
    )
    return run_id


async def pr(person_id, *, age=1, state="open", additions=0, deletions=0, **kwargs):
    pr_id = uuid4()
    await insert(
        "pr_projection",
        pr_id=pr_id,
        repository_id=uuid4(),
        opened_at=NOW - timedelta(days=age),
        current_state=state,
        model_attribution_quality="unavailable",
        **kwargs,
    )
    await insert(
        "pr_usage_projection",
        pr_id=pr_id,
        user_id=person_id,
        additions=additions,
        deletions=deletions,
        changed_files=1,
        observed_at=NOW,
        event_id=uuid4(),
        **{k: v for k, v in kwargs.items() if k == "workspace_id"},
    )
    return pr_id


async def report(**kwargs):
    return await queries.usage_leaderboard(
        **{"period": "7d", "limit": 100, "current_login": None, "current_email": None, **kwargs}
    )


async def test_empty_report_exposes_collection_progress(usage_db):
    result = await report()
    assert result["rows"] == []
    assert result["total_members"] == 0
    assert result["current_user_rank"] is None
    assert result["completeness"] == "not_started"
    assert result["data_source"] == "event_projections"
    assert result["collection_started_at"] is None
    assert result["reviewer_stats"]["reviewed_prs"] == 0
    assert result["reviewer_stats"]["resolution_rate"] == 0
    assert result["reviewer_stats"]["top_categories"] == []


async def test_usage_ranks_run_and_pr_cohorts_with_cost_coverage(usage_db):
    alice = await person("alice", "alice@example.com")
    bob = await person("bob", "bob@example.com")
    carol = await person("carol", "carol@example.com")
    stale = await person("stale")
    model = uuid4()
    await insert("model_directory", model_id=model, provider_model_id="model-a")
    first_run = await run(alice, duration=60, model_id=model, tokens=4)
    await run(alice, duration=120, model_id=model, tokens=7)
    await run(alice, duration=-1, tokens=3)
    await run(alice, tokens=6)
    await run(alice, age=8, duration=10000, tokens=9999)
    await run(alice, age=-1, duration=10000, tokens=9999)
    await run(stale, age=8)
    await run(carol)
    await insert(
        "latest_cost_projection",
        run_id=first_run,
        observation_revision=2,
        observed_at=NOW,
        status="partial",
        cost_usd=1.25,
        total_tokens=100,
        source="langsmith",
        event_id=uuid4(),
    )
    await pr(alice, additions=12, deletions=8)
    await pr(alice, age=8, state="merged", additions=500)
    await pr(bob, state="merged", additions=1, deletions=2)
    await pr(stale, age=8)
    foreign = uuid4()
    outsider = await person("outside", workspace_id=foreign)
    await run(outsider, workspace_id=foreign)
    await pr(outsider, state="merged", additions=10000, workspace_id=foreign)

    result = await report(limit=1, current_login=" ALICE ")
    assert result["total_members"] == 3
    assert result["current_user_rank"] == 2
    assert [r["rank"] for r in result["rows"]] == [1, 2]
    row = result["rows"][1]
    assert row["user"] == {"name": "alice", "github_login": "alice", "email": "alice@example.com"}
    assert row["invocations"] == row["agent_runs"] == 4
    assert row["avg_invocation_seconds"] == row["avg_run_seconds"] == 90
    assert row["favorite_model"] == "model-a"
    assert row["prs_opened"] == 1
    assert row["merged_prs"] == 0
    assert row["agent_loc"] == 20
    assert row["additions"] == 12
    assert row["deletions"] == 8
    assert row["total_tokens"] == 116
    assert row["total_cost_usd"] == 1.25
    assert row["invocations_without_cost"] == 3
    assert row["invocations_with_partial_cost"] == 1
    assert result["invocations_without_cost"] == 4
    assert result["rows"][0]["user"]["email"] is None
    assert result["rows"][0]["user"]["github_login"] is None
    assert (await report(limit=0))["rows"][0]["rank"] == 1


async def test_aliases_and_pr_only_members_preserve_privacy(usage_db):
    canonical = await person("named", "named@example.com")
    email_only = await person(email="private@example.com")
    alias = uuid4()
    await insert("identity_aliases", alias_person_id=alias, person_id=canonical)
    await run(alias)
    await run(canonical)
    await pr(alias, state="merged")
    await pr(email_only, additions=2)
    ordinary = await report()
    assert ordinary["total_members"] == 2
    assert ordinary["rows"][0]["invocations"] == 2
    assert ordinary["rows"][0]["prs_opened"] == 1
    assert ordinary["rows"][1]["user"] == {
        "name": "Open SWE user",
        "github_login": None,
        "email": None,
    }
    own = await report(limit=1, current_email=" PRIVATE@EXAMPLE.COM ")
    assert own["current_user_rank"] == 2
    assert own["rows"][1]["user"]["name"] == "private"
    assert own["rows"][1]["user"]["email"] == "private@example.com"
    admin = await report(admin=True)
    assert admin["rows"][0]["user"]["github_login"] == "named"
    assert admin["rows"][1]["user"]["name"] == "private"
    assert all(row["user"]["email"] is None for row in admin["rows"])


async def test_reviewer_uses_publication_recording_and_surfacing_cohorts(usage_db):
    reviewed_pr = uuid4()
    for pr_id, count, age in [
        (reviewed_pr, 2, 1),
        (reviewed_pr, 0, 2),
        (uuid4(), 0, 1),
        (uuid4(), 9, 8),
    ]:
        await insert(
            "review_projection",
            review_id=uuid4(),
            pr_id=pr_id,
            published_at=NOW - timedelta(days=age),
            finding_count=count,
        )
    initial = uuid4()
    changed = uuid4()
    findings = [
        # Recent surfaced finding resolved on a later revision.
        (1, 1, "resolved", "high", "security", initial, changed, 2),
        # Older recording remains in the recent surfaced cohort.
        (8, 1, "resolved", "medium", "correctness", initial, initial, 3),
        (1, 1, "dismissed", "low", "style", initial, None, 1),
        (1, 1, "open", "high", "security", initial, None, 4),
        # Unpublished findings contribute severity/category only.
        (1, None, "open", "critical", "security", initial, None, 20),
        # Older surfaced and future records belong to neither recent cohort.
        (8, 8, "resolved", "critical", "old", initial, changed, 50),
        (-1, -1, "resolved", "critical", "future", initial, changed, 50),
    ]
    for recorded_age, surfaced_age, state, severity, category, first, resolved, replies in findings:
        await insert(
            "finding_usage_projection",
            finding_id=uuid4(),
            pr_id=uuid4(),
            repository_id=uuid4(),
            recorded_at=NOW - timedelta(days=recorded_age),
            surfaced_at=NOW - timedelta(days=surfaced_age) if surfaced_age is not None else None,
            current_state=state,
            severity=severity,
            category=category,
            first_seen_revision_id=first,
            resolved_revision_id=resolved,
            human_replies=replies,
            observed_at=NOW,
            event_id=uuid4(),
        )
    await insert(
        "review_projection",
        workspace_id=uuid4(),
        review_id=uuid4(),
        pr_id=uuid4(),
        published_at=NOW,
        finding_count=20,
    )
    stats = (await report())["reviewer_stats"]
    assert stats["reviewed_prs"] == 2
    assert stats["prs_with_findings"] == 1
    assert stats["findings_recorded"] == 4
    assert stats["surfaced_findings"] == 4
    assert stats["addressed_findings"] == 2
    assert stats["resolved_after_update"] == 1
    assert stats["dismissed_findings"] == 1
    assert stats["unresolved_surfaced_findings"] == 1
    assert stats["resolution_rate"] == 0.5
    assert stats["human_replies"] == 10
    assert stats["severity_counts"] == {"high": 2, "low": 1, "critical": 1}
    assert stats["top_categories"] == [
        {"name": "security", "count": 3},
        {"name": "style", "count": 1},
    ]


async def test_reports_require_activation_and_exclude_pre_cutover_facts(usage_db):
    user = await person("member")
    await run(user, age=2)
    await run(user, age=0)
    await pr(user, age=2, state="merged")
    await pr(user, age=0, additions=3)
    for age in (0, 2):
        await insert(
            "review_projection",
            review_id=uuid4(),
            pr_id=uuid4(),
            published_at=NOW - timedelta(days=age),
            finding_count=1,
        )
        await insert(
            "finding_usage_projection",
            finding_id=uuid4(),
            pr_id=uuid4(),
            repository_id=uuid4(),
            recorded_at=NOW - timedelta(days=age),
            surfaced_at=NOW - timedelta(days=age),
            current_state="open",
            severity="high",
            category="security",
            human_replies=1,
            observed_at=NOW,
            event_id=uuid4(),
        )
    async with database.transaction() as conn:
        await conn.execute(
            text("UPDATE deployment_metadata SET reporting_cutover_at = :cutover"),
            {"cutover": NOW - timedelta(days=1)},
        )
    result = await report(period="all")
    assert result["rows"][0]["invocations"] == 1
    assert result["rows"][0]["prs_opened"] == 1
    assert result["rows"][0]["merged_prs"] == 0
    assert result["reviewer_stats"]["reviewed_prs"] == 1
    assert result["reviewer_stats"]["findings_recorded"] == 1
    assert result["reviewer_stats"]["surfaced_findings"] == 1
    assert result["reviewer_stats"]["human_replies"] == 1
    assert result["reporting_cutover_at"] == (NOW - timedelta(days=1)).isoformat()
    outcomes = await queries.pr_merge_rate_by_model(period="all", admin=True)
    assert outcomes["cohorts"][0]["cohort_size"] == 1
    async with database.transaction() as conn:
        await conn.execute(text("UPDATE deployment_metadata SET reporting_cutover_at = NULL"))
    with pytest.raises(RuntimeError, match="not been activated"):
        await report()
    with pytest.raises(RuntimeError, match="not been activated"):
        await queries.pr_merge_rate_by_model(period="all")


@pytest.mark.parametrize("reuse", ["login", "email", "both"])
async def test_reused_handles_do_not_transfer_an_immutable_owners_usage(usage_db, reuse):
    provisional = await directory.resolve_person(github_login="original", email="old@example.com")
    await run(provisional)
    original = await directory.resolve_person(
        immutable_person_key=123, github_login="original", email="old@example.com"
    )
    await run(original)
    login = "original" if reuse in {"login", "both"} else "new-owner"
    email = "old@example.com" if reuse in {"email", "both"} else "new@example.com"
    new_owner = await directory.resolve_person(
        immutable_person_key=456, github_login=login, email=email
    )
    assert new_owner != original
    assert await directory.resolve_person(github_login=login, email=email) == new_owner
    if reuse in {"login", "both"}:
        assert await directory.resolve_person(github_login=login) == new_owner
    if reuse in {"email", "both"}:
        assert await directory.resolve_person(email=email) == new_owner
    await run(new_owner)
    result = await report(current_login=login, current_email=email)
    assert result["total_members"] == 2
    assert sorted(row["invocations"] for row in result["rows"]) == [1, 2]
    own = next(row for row in result["rows"] if row["rank"] == result["current_user_rank"])
    assert own["invocations"] == 1
    async with database.connection() as conn:
        assert (
            await conn.scalar(
                text("SELECT person_id FROM identity_aliases WHERE alias_person_id = :alias"),
                {"alias": provisional},
            )
            == original
        )
    assert await directory.resolve_person(immutable_person_key=123) == original
