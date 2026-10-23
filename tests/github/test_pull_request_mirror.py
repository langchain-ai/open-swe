"""PostgreSQL regressions for the pull request mirror: deliveries in, dashboard views out."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, update

from openswe.database import postgres
from openswe.github import pull_request_dashboard_routes
from openswe.github.mirror import views
from openswe.github.mirror.ci import CheckRun
from openswe.github.mirror.conversation import IssueComment, IssueCommentPayload
from openswe.github.mirror.ingest import ingest
from openswe.github.mirror.sync import _Snapshot, prune_ci
from openswe.github.mirror.views import PullRequestView
from openswe.github.pull_requests import PullRequest
from openswe.github.repos import ReadableRepositories
from openswe.jobs import queue

pytestmark = pytest.mark.usefixtures("registry_db")

REPOSITORY = {"owner": {"login": "lc"}, "name": "repo", "full_name": "lc/repo"}
HEAD = "a" * 40
T0 = datetime(2026, 10, 1, tzinfo=UTC)


def _pull(**fields: object) -> dict[str, object]:
    return {
        "number": 7,
        "id": 70,
        "title": "Add widget",
        "state": "open",
        "user": {"login": "ada", "id": 1},
        "head": {"ref": "widget", "sha": HEAD},
        "base": {"ref": "main", "sha": "b" * 40},
        "updated_at": T0.isoformat(),
        "labels": [{"name": "bug", "color": "f00"}],
        "requested_reviewers": [],
        "requested_teams": [],
        **fields,
    }


async def _deliver(event: str, action: str, **payload: object) -> None:
    await ingest(event, {"action": action, "repository": REPOSITORY, **payload})


async def _view() -> PullRequestView:
    stored = await PullRequest.get("lc", "repo", 7)
    assert stored is not None
    (view,) = await PullRequestView.load([stored])
    return view


async def test_an_older_or_abridged_description_never_overwrites_a_newer_one() -> None:
    later = (T0 + timedelta(minutes=1)).isoformat()
    await _deliver(
        "pull_request",
        "edited",
        pull_request=_pull(title="Retitled", updated_at=later, additions=9),
    )
    await _deliver("pull_request", "edited", pull_request=_pull(title="Stale"))
    # A review delivery carries an abridged PR: no diff stats, no labels.
    abridged = {k: v for k, v in _pull(updated_at=later).items() if k != "labels"}
    await _deliver(
        "pull_request_review",
        "submitted",
        pull_request={**abridged, "title": "Retitled"},
        review={"id": 5, "state": "approved", "user": {"login": "bo", "id": 2}},
    )

    stored = await PullRequest.get("lc", "repo", 7)
    assert stored is not None
    assert (stored.title, stored.additions, stored.labels) == (
        "Retitled",
        9,
        [{"name": "bug", "color": "f00", "description": ""}],
    )
    assert [r.login for r in views.PullRequestReviewer.latest((await _view()).reviews)] == ["bo"]


async def test_a_new_head_forgets_mergeability_until_github_decides_again() -> None:
    await _deliver(
        "pull_request", "opened", pull_request=_pull(mergeable=True, mergeable_state="clean")
    )
    pushed = _pull(
        head={"ref": "widget", "sha": "c" * 40},
        mergeable=None,
        updated_at=(T0 + timedelta(minutes=1)).isoformat(),
    )
    await _deliver("pull_request", "synchronize", pull_request=pushed)

    stored = await PullRequest.get("lc", "repo", 7)
    assert stored is not None and (stored.head_sha, stored.mergeable) == ("c" * 40, None)
    assert await queue.get("pull_request_sync:hydrate:lc/repo/7") is not None


async def test_head_checks_never_regress_and_drop_superseded_cancellations() -> None:
    await _deliver("pull_request", "opened", pull_request=_pull())

    def run(github_id: int, suite: int, **fields: object) -> dict[str, object]:
        return {
            "id": github_id,
            "head_sha": HEAD,
            "name": "test",
            "status": "completed",
            "app": {"id": 15368, "slug": "github-actions"},
            "check_suite": {"id": suite},
            **fields,
        }

    for workflow_run, suite in ((100, 10), (101, 11)):
        await _deliver(
            "workflow_run",
            "completed",
            workflow_run={
                "id": workflow_run,
                "head_sha": HEAD,
                "workflow_id": 9,
                "check_suite_id": suite,
                "event": "pull_request",
            },
        )
    await _deliver("check_run", "completed", check_run=run(1, 10, conclusion="cancelled"))
    await _deliver("check_run", "completed", check_run=run(2, 11, conclusion="failure"))
    # A late delivery from before the run finished.
    await _deliver("check_run", "created", check_run=run(2, 11, status="in_progress"))
    await _deliver(
        "status", "", sha=HEAD, context="ci/legacy", state="pending", updated_at=T0.isoformat()
    )

    checks = (await _view()).checks
    assert [(r.github_id, r.conclusion) for r in checks.runs] == [(2, "failure")]
    assert (checks.state, checks.pending) == ("failing", ["ci/legacy"])
    assert [check.name for check in checks.failing] == ["test"]


@pytest.mark.parametrize(
    ("conclusion", "state", "inconclusive"),
    [
        ("cancelled", "unknown", 1),
        ("skipped", "passing", 1),
        ("success", "passing", 0),
        ("timed_out", "failing", 0),
    ],
)
async def test_head_check_verdicts(conclusion: str, state: str, inconclusive: int) -> None:
    await _deliver("pull_request", "opened", pull_request=_pull())
    await _deliver(
        "check_run",
        "completed",
        check_run={
            "id": 1,
            "head_sha": HEAD,
            "name": "lint",
            "status": "completed",
            "conclusion": conclusion,
        },
    )
    checks = (await _view()).checks
    assert (checks.state, checks.inconclusive) == (state, inconclusive)


async def test_listing_scopes_stay_inside_the_viewers_repositories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _deliver("pull_request", "opened", pull_request=_pull())
    await _deliver(
        "pull_request",
        "opened",
        pull_request=_pull(
            number=8,
            id=80,
            user={"login": "bo", "id": 2},
            requested_reviewers=[{"login": "ada", "id": 1}],
        ),
    )
    await _deliver(
        "pull_request",
        "opened",
        pull_request=_pull(
            number=9, id=90, user={"login": "bo", "id": 2}, requested_teams=[{"slug": "core"}]
        ),
    )
    await ingest(
        "pull_request",
        {
            "action": "opened",
            "repository": {**REPOSITORY, "name": "secret", "full_name": "lc/secret"},
            "pull_request": _pull(number=1, id=10),
        },
    )
    monkeypatch.setattr(
        ReadableRepositories,
        "of",
        AsyncMock(return_value=ReadableRepositories(frozenset({"lc/repo"}))),
    )
    monkeypatch.setattr(views, "team_members", AsyncMock(return_value=["Ada"]))

    async def listed(scope: str, repo: str = "") -> list[tuple[str, int]]:
        page = await pull_request_dashboard_routes.api_list_pull_requests(
            repo=repo,
            sort="updated",
            direction="desc",
            page=1,
            scope=scope,
            session={"sub": "ada"},
        )
        return [(row.repo, row.number) for row in page.pull_requests]

    assert await listed("mine") == [("lc/repo", 7)]
    assert sorted(await listed("review-requested")) == [("lc/repo", 8), ("lc/repo", 9)]
    assert await listed("mine", repo="lc/secret") == []


async def test_thread_health_lists_only_unresolved_threads_with_their_replies() -> None:
    await _deliver("pull_request", "opened", pull_request=_pull())

    def comment(github_id: int, **fields: object) -> dict[str, object]:
        return {
            "id": github_id,
            "user": {"login": "bo", "id": 2},
            "body": f"comment {github_id}",
            "path": "widget.py",
            "line": 3,
            "created_at": T0.isoformat(),
            **fields,
        }

    pull = _pull()
    await _deliver("pull_request_review_comment", "created", pull_request=pull, comment=comment(1))
    await _deliver(
        "pull_request_review_comment",
        "created",
        pull_request=pull,
        comment=comment(2, in_reply_to_id=1, user={"login": "ada", "id": 1}),
    )
    await _deliver("pull_request_review_comment", "created", pull_request=pull, comment=comment(3))
    await _deliver(
        "pull_request_review_thread",
        "resolved",
        pull_request=pull,
        thread={"node_id": "PRRT_3", "comments": [comment(3)]},
    )
    async with postgres.session() as session:
        stored = await PullRequest.get("lc", "repo", 7)
        assert stored is not None
        await session.execute(
            update(PullRequest)
            .where(PullRequest.id == stored.id)
            .values(synced_at=func.clock_timestamp())
        )

    health = await (await _view()).health()

    assert health["unresolvedReviewThreadCount"] == 1
    (thread,) = health["unresolvedReviewThreads"]
    assert (thread["body"], [reply["author"] for reply in thread["replies"]]) == (
        "comment 1",
        ["ada"],
    )


async def test_hydrate_drops_what_github_no_longer_lists_but_keeps_newer_deliveries() -> None:
    await _deliver("pull_request", "opened", pull_request=_pull())
    stored = await PullRequest.get("lc", "repo", 7)
    assert stored is not None

    def comment(github_id: int) -> IssueCommentPayload:
        return IssueCommentPayload.model_validate(
            {"id": github_id, "body": "hi", "user": {"login": "bo", "id": 2}}
        )

    async with postgres.session() as session:
        await IssueComment.store(session, [comment(1).to_row(stored.id)])
    async with postgres.transaction() as conn:
        read_at = (await conn.execute(select(func.clock_timestamp()))).scalar_one()
    async with postgres.session() as session:
        await IssueComment.store(session, [comment(2).to_row(stored.id)])

    empty = _Snapshot([], [], [], [], [], [], [], [], [])
    await empty.store(stored, HEAD, read_at=read_at)

    async with postgres.session() as session:
        kept = await session.scalars(select(IssueComment.github_id))
        assert list(kept) == [2]


async def test_pruning_keeps_ci_on_open_heads_and_drops_old_ci_elsewhere() -> None:
    await _deliver("pull_request", "opened", pull_request=_pull())
    for github_id, sha in ((1, HEAD), (2, "d" * 40), (3, "e" * 40)):
        await _deliver(
            "check_run",
            "completed",
            check_run={"id": github_id, "head_sha": sha, "name": "ci", "status": "completed"},
        )
    async with postgres.session() as session:
        await session.execute(
            update(CheckRun)
            .where(CheckRun.github_id.in_([1, 2]))
            .values(updated_at=func.clock_timestamp() - timedelta(days=30))
        )

    await prune_ci()

    async with postgres.session() as session:
        assert sorted(await session.scalars(select(CheckRun.github_id))) == [1, 3]
