"""PostgreSQL reporting preserves usage cohorts, identities, and disclosure rules."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from agent.analytics import directory, queries
from agent.database import analytics as database
from agent.database import postgres
from tests.analytics.conftest import initialize_database

NOW = datetime(2026, 9, 11, tzinfo=UTC)


@pytest.fixture
async def usage_db(deployment_db, monkeypatch):
    await initialize_database()
    async with postgres.transaction() as conn:
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
    async with postgres.transaction() as conn:
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
    alice = await person("alice", "alice@example.com", display_name="Alice Example")
    bob = await person("bob", "bob@example.com")
    carol = await person("carol", "carol@example.com")
    stale = await person("stale")
    model = uuid4()
    await insert("model_directory", model_id=model, provider_model_id="model-a")
    first_thread = uuid4()
    second_thread = uuid4()
    first_run = await run(
        alice,
        duration=60,
        model_id=model,
        configured_effort="high",
        tokens=4,
        thread_id=first_thread,
    )
    await run(
        alice,
        duration=120,
        model_id=model,
        configured_effort="high",
        tokens=7,
        thread_id=first_thread,
    )
    await run(alice, duration=-1, tokens=3, thread_id=second_thread)
    await run(alice, tokens=6, thread_id=second_thread)
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

    result = await report(limit=2, current_login=" ALICE ")
    assert result["total_members"] == 3
    assert result["current_user_rank"] == 2
    assert [r["rank"] for r in result["rows"]] == [1, 2]
    row = result["rows"][1]
    assert row["user"] == {
        "name": "Alice Example",
        "github_login": "alice",
        "email": "alice@example.com",
        "avatar_url": "https://github.com/alice.png?size=80",
    }
    assert row["invocations"] == row["agent_runs"] == 4
    assert row["threads"] == 2
    assert row["avg_invocation_seconds"] == row["avg_run_seconds"] == 90
    assert row["avg_thread_seconds"] == 180
    assert row["favorite_model"] == "model-a"
    assert row["favorite_model_effort"] == "high"
    assert row["prs_opened"] == 1
    assert row["merged_prs"] == 0
    assert row["merged_prs_per_thread"] == 0
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
    assert [row["rank"] for row in (await report(limit=1, offset=1))["rows"]] == [2]


async def test_usage_sorting_happens_before_pagination(usage_db):
    alice = await person("alice", display_name="Alice")
    bob = await person("bob", display_name="bob")
    carol = await person("carol", display_name="Carol")
    await run(alice, tokens=10)
    await run(bob, tokens=30)
    await run(carol, tokens=20)

    first = await report(limit=2, sort="total_tokens", direction="desc")
    assert [row["user"]["name"] for row in first["rows"]] == ["bob", "Carol"]
    second = await report(
        limit=2,
        cursor=first["next_cursor"],
        sort="total_tokens",
        direction="desc",
    )
    assert [row["user"]["name"] for row in second["rows"]] == ["Alice"]
    with pytest.raises(ValueError, match="invalid usage leaderboard cursor"):
        await report(
            limit=2,
            cursor=first["next_cursor"],
            sort="user",
            direction="asc",
        )


@pytest.mark.parametrize("direction", ["asc", "desc"])
async def test_avg_invocations_per_thread_sort_handles_members_without_threads(
    usage_db: UUID, direction: queries.SortDirection
) -> None:
    dense = await person("dense", display_name="Dense")
    sparse = await person("sparse", display_name="Sparse")
    threadless = await person("threadless", display_name="Threadless")
    shared_thread = uuid4()
    await run(dense, thread_id=shared_thread)
    await run(dense, thread_id=shared_thread)
    await run(sparse, thread_id=uuid4())
    await run(threadless)
    await pr(threadless, state="merged")

    result = await report(sort="avg_invocations_per_thread", direction=direction)
    averages = {row["user"]["name"]: row["avg_invocations_per_thread"] for row in result["rows"]}
    assert averages == {"Dense": 2, "Sparse": 1, "Threadless": 0}
    expected = ["Threadless", "Sparse", "Dense"]
    if direction == "desc":
        expected.reverse()
    assert [row["user"]["name"] for row in result["rows"]] == expected


async def test_usage_sorts_by_merged_prs_per_thread(usage_db):
    alice = await person("alice")
    bob = await person("bob")
    await run(alice, thread_id=uuid4())
    for _ in range(2):
        await pr(alice, state="merged")
    for _ in range(2):
        await run(bob, thread_id=uuid4())
    for _ in range(3):
        await pr(bob, state="merged")

    result = await report(sort="merged_prs_per_thread", direction="desc")

    assert [row["user"]["name"] for row in result["rows"]] == ["alice", "bob"]
    assert [row["merged_prs_per_thread"] for row in result["rows"]] == [2, 1.5]


async def test_user_sort_follows_disclosed_names_not_hidden_ones(usage_db):
    # Hidden members are ordered by the label the viewer sees, so their real names
    # cannot be inferred from where they land in the list.
    zeta = await person(email="zeta@example.com")
    alpha = await person(email="alpha@example.com")
    mid = await person("mid")
    for member in (zeta, alpha, mid):
        await run(member)
    await pr(zeta, state="merged")

    ordinary = await report(sort="user", direction="asc", anonymize_others=True)
    assert [row["user"]["name"] for row in ordinary["rows"]] == ["Open SWE user"] * 3
    # Masked members tie on their shared label and fall back to rank, not to "alpha"
    # before "zeta".
    assert [row["rank"] for row in ordinary["rows"]] == [1, 2, 3]

    own = await report(sort="user", direction="asc", current_login="mid", anonymize_others=True)
    assert [row["user"]["name"] for row in own["rows"]] == [
        "mid",
        "Open SWE user",
        "Open SWE user",
    ]
    assert [row["rank"] for row in own["rows"]] == [3, 1, 2]

    admin = await report(sort="user", direction="asc", admin=True, anonymize_others=True)
    assert [row["user"]["name"] for row in admin["rows"]] == ["alpha", "mid", "zeta"]


@pytest.mark.parametrize("direction", ["asc", "desc"])
async def test_favorite_models_sort_by_displayed_labels_before_pagination(
    usage_db: UUID, direction: queries.SortDirection
) -> None:
    # Alphabetical display order, including sanitizing, truncation ties, and fallbacks.
    models = [
        "provider/---Alpha---",
        None,
        "fireworks:accounts/fireworks/models/kimi-k3",
        "google_genai:gemini-3.8-flash",
        "provider/" + "m" * 48 + "z",
        "provider/" + "m" * 48 + "a",
        "provider/model space",
        "///",
        "provider/Zulu",
    ]
    for index, provider_model_id in enumerate(models):
        member = await person(f"member-{index}")
        model_id = None
        if provider_model_id is not None:
            model_id = uuid4()
            await insert("model_directory", model_id=model_id, provider_model_id=provider_model_id)
        await run(member, model_id=model_id)

    expected = list(range(len(models)))
    if direction == "desc":
        expected.reverse()
        # Equal displayed labels retain rank order in either direction.
        expected[3:5] = [4, 5]
    cursor = None
    actual: list[str] = []
    while True:
        page = await report(limit=2, cursor=cursor, sort="favorite_model", direction=direction)
        actual.extend(row["favorite_model"] for row in page["rows"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert actual == [models[index] or "default" for index in expected]


async def test_favorite_model_effort_is_scoped_to_the_favorite_model(usage_db):
    reader = await person("reader")
    favorite_model = uuid4()
    other_model = uuid4()
    await insert("model_directory", model_id=favorite_model, provider_model_id="favorite")
    await insert("model_directory", model_id=other_model, provider_model_id="other")
    await run(reader, model_id=favorite_model, configured_effort="high")
    await run(reader, model_id=favorite_model, configured_effort="medium")
    await run(reader, model_id=favorite_model, configured_effort="medium")
    for _ in range(4):
        await run(reader, model_id=other_model, configured_effort="max")
    for _ in range(3):
        await run(reader, model_id=favorite_model, configured_effort="high")

    row = (await report())["rows"][0]
    assert row["favorite_model"] == "favorite"
    assert row["favorite_model_effort"] == "high"


async def test_favorite_model_effort_reports_missing_legacy_values(usage_db):
    reader = await person("reader")
    model = uuid4()
    await insert("model_directory", model_id=model, provider_model_id="favorite")
    await run(reader, model_id=model)

    row = (await report())["rows"][0]
    assert row["favorite_model"] == "favorite"
    assert row["favorite_model_effort"] is None


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
    # With the policy off a login-less, name-less member's email prefix is the
    # disclosed label; the full email is still never shown.
    assert ordinary["rows"][1]["user"] == {
        "name": "private",
        "github_login": None,
        "email": None,
        "avatar_url": None,
    }
    own = await report(limit=2, current_email=" PRIVATE@EXAMPLE.COM ")
    assert own["current_user_rank"] == 2
    assert own["rows"][1]["user"]["name"] == "private"
    assert own["rows"][1]["user"]["email"] == "private@example.com"
    admin = await report(admin=True)
    assert admin["rows"][0]["user"]["github_login"] == "named"
    assert admin["rows"][1]["user"]["name"] == "private"
    assert all(row["user"]["email"] is None for row in admin["rows"])


async def test_privacy_enabled_hides_other_members_identities_completely(usage_db):
    alice = await person("alice", "alice@example.com", display_name="Alice Example")
    bob = await person("bob", "bob@example.com")
    carol = await person(email="carol@example.com")
    dan = await person(email="dan@example.com", display_name="Dan Named")
    for member in (alice, bob, carol, dan):
        await run(member)

    own = await report(
        current_login="alice", current_email="alice@example.com", anonymize_others=True
    )
    assert own["total_members"] == 4
    mine = next(row for row in own["rows"] if row["rank"] == own["current_user_rank"])
    # The viewer keeps their own identity, avatar and all.
    assert mine["user"] == {
        "name": "Alice Example",
        "github_login": "alice",
        "email": "alice@example.com",
        "avatar_url": "https://github.com/alice.png?size=80",
    }
    others = [row for row in own["rows"] if row is not mine]
    # Other members are fully anonymous: no login, email, avatar, or any
    # name that could single someone out — not even the email-prefix fallback.
    assert [row["user"] for row in others] == [
        {"name": "Open SWE user", "github_login": None, "email": None, "avatar_url": None}
    ] * 3
    # Metrics, rank, and pagination are untouched by the policy.
    assert sorted(row["invocations"] for row in own["rows"]) == [1, 1, 1, 1]
    assert [row["rank"] for row in own["rows"]] == [1, 2, 3, 4]
    page_two = await report(limit=1, offset=1, anonymize_others=True)
    assert [row["rank"] for row in page_two["rows"]] == [2]

    # Admins keep seeing everyone, identified, even with the policy on — and a
    # login-less member's stored display name still identifies them.
    admin = await report(anonymize_others=True, admin=True)
    assert sorted(row["user"]["name"] for row in admin["rows"]) == [
        "Alice Example",
        "Dan Named",
        "bob",
        "carol",
    ]
    assert admin["rows"][0]["user"]["avatar_url"] is not None

    # Policy off: a signed-in member sees rows with a login identified; a
    # login-less row keeps its stored display name (dan) or, without one, the
    # email prefix (carol) — never the full email. Emails stay own-row only.
    off = await report(anonymize_others=False)
    assert sorted(row["user"]["name"] for row in off["rows"]) == [
        "Alice Example",
        "Dan Named",
        "bob",
        "carol",
    ]
    assert all(row["user"]["email"] is None for row in off["rows"])
    assert all("example.com" not in row["user"]["name"] for row in off["rows"])
    assert off["rows"][0]["user"]["github_login"] is None


async def test_viewer_without_a_login_sees_their_display_name(usage_db):
    viewer = await person(email="viewer@example.com", display_name="Viewer Name")
    other = await person(email="other@example.com", display_name="Other Name")
    for member in (viewer, other):
        await run(member)

    own = await report(current_email="viewer@example.com", anonymize_others=True)
    names = sorted(row["user"]["name"] for row in own["rows"])
    assert names == ["Open SWE user", "Viewer Name"]
    mine = next(row for row in own["rows"] if row["user"]["name"] == "Viewer Name")
    assert mine["user"] == {
        "name": "Viewer Name",
        "github_login": None,
        "email": "viewer@example.com",
        "avatar_url": None,
    }


async def test_privacy_enabled_sorts_by_disclosed_labels_so_hidden_names_do_not_leak(usage_db):
    zeta = await person(email="zeta@example.com")
    alpha = await person(email="alpha@example.com")
    mid = await person("mid")
    for member in (zeta, alpha, mid):
        await run(member)
    await pr(zeta, state="merged")

    result = await report(sort="user", direction="asc", anonymize_others=True)
    # Without a viewer identity every row anonymizes: they tie on the shared
    # label and order by rank, never by the hidden alpha-before-zeta names.
    assert [row["user"]["name"] for row in result["rows"]] == ["Open SWE user"] * 3
    assert [row["rank"] for row in result["rows"]] == [1, 2, 3]

    own = await report(sort="user", direction="asc", current_login="mid", anonymize_others=True)
    assert [row["user"]["name"] for row in own["rows"]] == [
        "mid",
        "Open SWE user",
        "Open SWE user",
    ]
    assert [row["rank"] for row in own["rows"]] == [3, 1, 2]

    # Policy off: login-less members with no stored display name fall back to
    # their disclosed email prefixes, which order the report too.
    off = await report(sort="user", direction="asc")
    assert [row["user"]["name"] for row in off["rows"]] == ["alpha", "mid", "zeta"]
    assert [row["rank"] for row in off["rows"]] == [2, 3, 1]


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
    async with postgres.transaction() as conn:
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
    assert outcomes["cohorts"] == []
    async with postgres.transaction() as conn:
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
    async with postgres.connection() as conn:
        assert (
            await conn.scalar(
                text("SELECT person_id FROM identity_aliases WHERE alias_person_id = :alias"),
                {"alias": provisional},
            )
            == original
        )
    assert await directory.resolve_person(immutable_person_key=123) == original
