"""Who may approve a pull request Robin authored as itself (``Robin Approves``)."""

import asyncio
import importlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openswe.github.approval_policy import PullRequestApproval
from openswe.github.ci import RequiredCheck
from openswe.github.http import GitHubClient
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequest
from openswe.github.repositories import Repository

_ROBIN = {"login": "open-swe[bot]", "type": "Bot"}
_HEAD = "head"


def _review(
    login: str,
    state: str = "APPROVED",
    sha: str = _HEAD,
    kind: str = "User",
) -> dict:
    return {
        "user": {"login": login, "type": kind, "id": len(login)},
        "state": state,
        "commit_id": sha,
    }


def _commit(login: str) -> dict:
    return {"author": {"login": login}, "committer": {"login": "web-flow"}}


@pytest.mark.parametrize(
    ("reviews", "commits", "passed"),
    [
        pytest.param([_review("alice")], [], False, id="the-requester-approves-their-own-ask"),
        pytest.param([_review("Bob")], [_commit("bob")], False, id="a-pusher-approves"),
        pytest.param([_review("carol", sha="old")], [], False, id="approval-of-an-older-head"),
        pytest.param(
            [_review("carol"), _review("carol", "CHANGES_REQUESTED")], [], False, id="withdrawn"
        ),
        pytest.param([_review("other[bot]", kind="Bot")], [], False, id="another-bot-approves"),
        pytest.param([_review("dave")], [], False, id="no-write-access"),
        pytest.param([_review("alice2")], [], False, id="the-requester-renamed"),
        pytest.param([_review("carol")], [_commit("alice")], True, id="someone-else-approves"),
        pytest.param([_review("open-swe[bot]", kind="Bot")], [], True, id="robin-approves-its-own"),
    ],
)
def test_a_robin_authored_pr_needs_an_approval_from_outside_its_requester_and_pushers(
    reviews: list[dict], commits: list[dict], passed: bool
) -> None:
    approval = PullRequestApproval(
        author=_ROBIN,
        head_sha=_HEAD,
        requesters=frozenset({"Alice"}),
        requester_ids=frozenset({len("alice2")}),
        reviews=reviews,
        commits=commits,
        writers=frozenset({"alice", "alice2", "bob", "carol"}),
    )
    assert approval.verdict()[0] is passed


def test_a_person_authored_pr_is_left_to_the_repositorys_rules() -> None:
    approval = PullRequestApproval(author={"login": "alice", "type": "User"}, head_sha=_HEAD)
    assert approval.verdict()[0] is True


def test_robins_auto_approval_of_the_head_lets_the_author_alone_merge() -> None:
    approval = PullRequestApproval(
        author=_ROBIN, head_sha=_HEAD, requesters=frozenset({"alice"}), auto_approved=True
    )
    assert approval.verdict()[0] is True


def test_a_pr_opened_before_requesters_were_kept_stays_blocked() -> None:
    approval = PullRequestApproval(
        author=_ROBIN,
        head_sha=_HEAD,
        reviews=[_review("carol")],
        writers=frozenset({"carol"}),
        requester_unknown=True,
    )
    assert approval.verdict()[0] is False


def _client(
    monkeypatch: pytest.MonkeyPatch, state: str, author: dict[str, str] | None = None
) -> SimpleNamespace:
    pull = SimpleNamespace(
        repo=SimpleNamespace(
            owner="acme", name="app", full_name="acme/app", create_check_run=AsyncMock()
        ),
        number=7,
        pull=AsyncMock(
            return_value={
                "state": state,
                "head": {"sha": _HEAD},
                "base": {"ref": "main"},
                "user": author or {"login": "alice", "type": "User"},
            }
        ),
        reviews=AsyncMock(return_value=[]),
        contribution_commits=AsyncMock(return_value=[]),
        issue_comments=AsyncMock(return_value=[]),
        comment=AsyncMock(),
    )

    @asynccontextmanager
    async def as_app(*_: object):
        yield pull

    monkeypatch.setattr(PullRequestClient, "as_app", as_app)
    monkeypatch.setattr(PullRequest, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(PullRequest, "requesters", AsyncMock(return_value=([], [])))
    return pull


@pytest.mark.parametrize(
    ("author", "conclusion"),
    [
        pytest.param({"login": "alice", "type": "User"}, "success", id="person-authored"),
        pytest.param(_ROBIN, "failure", id="robin-authored-unapproved"),
        pytest.param(
            {"login": "robin-agent[bot]", "type": "Bot"}, "failure", id="robin-production"
        ),
        pytest.param(
            {"login": "open-swe-preview[bot]", "type": "Bot"}, "failure", id="robin-preview"
        ),
        pytest.param({"login": "openswe-dev[bot]", "type": "Bot"}, "failure", id="robin-dev"),
        pytest.param(
            {"login": "dependabot[bot]", "type": "Bot"}, "success", id="other-bot-authored"
        ),
    ],
)
@pytest.mark.parametrize("disabled", [None, "false", "true"])
@pytest.mark.parametrize("force_publish", [False, True])
async def test_the_check_is_posted_where_the_base_requires_it(
    monkeypatch: pytest.MonkeyPatch,
    author: dict[str, str],
    conclusion: str,
    disabled: str | None,
    force_publish: bool,
) -> None:
    if disabled is None:
        monkeypatch.delenv("DISABLE_ROBIN_APPROVES", raising=False)
    else:
        monkeypatch.setenv("DISABLE_ROBIN_APPROVES", disabled)
    required = AsyncMock(return_value=True)
    monkeypatch.setattr(Repository, "requires_approval_check", required)
    pull = _client(monkeypatch, "open", author)
    await PullRequestApproval.evaluate("acme", "app", 7, force_publish=force_publish)
    if disabled == "true":
        assert await PullRequestApproval.evaluate("acme", "app", 7, force_publish=True) is None
        pull.pull.assert_not_awaited()
        pull.repo.create_check_run.assert_not_awaited()
        pull.comment.assert_not_awaited()
        return
    if not force_publish:
        required.assert_awaited_once_with("acme", "app", "main", include_any_source=True)
    (check,), _ = pull.repo.create_check_run.call_args
    assert (check["name"], check["head_sha"], check["conclusion"]) == (
        "Robin Approves",
        _HEAD,
        conclusion,
    )
    assert pull.comment.await_count == (conclusion == "failure")


@pytest.mark.parametrize("required", [False, None])
@pytest.mark.parametrize("force_publish", [False, True])
async def test_optional_publication_does_not_override_the_approval_verdict(
    monkeypatch: pytest.MonkeyPatch,
    required: bool | None,
    force_publish: bool,
) -> None:
    pull = _client(monkeypatch, "open", _ROBIN)
    monkeypatch.setattr(Repository, "requires_approval_check", AsyncMock(return_value=required))
    result = await PullRequestApproval.evaluate("acme", "app", 7, force_publish=force_publish)
    if force_publish:
        assert result is not None and result[0] is False
        (check,), _ = pull.repo.create_check_run.call_args
        assert check["conclusion"] == "failure"
        (note,), _ = pull.comment.call_args
        assert "does not pass" in note
        assert "blocking this merge" not in note
        assert "Repository rules determine whether this check is required" in note
    else:
        assert result is None
        pull.repo.create_check_run.assert_not_awaited()
        pull.comment.assert_not_awaited()


@pytest.mark.parametrize(
    ("checks", "include_any_source", "expected"),
    [
        pytest.param({RequiredCheck("Robin Approves")}, True, True, id="any-source-publishing"),
        pytest.param({RequiredCheck("Robin Approves")}, False, False, id="any-source-creation"),
        pytest.param({RequiredCheck("Robin Approves", 42)}, True, True, id="our-app"),
        pytest.param({RequiredCheck("Robin Approves", 99)}, True, False, id="another-app"),
        pytest.param(set(), True, False, id="not-required"),
        pytest.param(None, True, None, id="unknown"),
    ],
)
async def test_requirement_sources_do_not_weaken_pr_creation(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    checks: set[RequiredCheck] | None,
    include_any_source: bool,
    expected: bool | None,
) -> None:
    monkeypatch.setenv("GITHUB_APP_ID", "42")
    monkeypatch.setattr(Repository, "resolve_default_branch", AsyncMock(return_value="main"))
    github = MagicMock()
    github.__aenter__ = AsyncMock(return_value=github)
    github.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(GitHubClient, "as_app", MagicMock(return_value=github))
    monkeypatch.setattr(RequiredCheck, "for_branch", AsyncMock(return_value=checks))
    kwargs = {"include_any_source": True} if include_any_source else {}
    branch = request.node.name
    assert await Repository.requires_approval_check("acme", "app", branch, **kwargs) is expected


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("refresh", [None, False, True])
async def test_publishing_uses_stale_rules_while_revalidating(
    monkeypatch: pytest.MonkeyPatch, cached: bool, refresh: bool | None
) -> None:
    from langgraph_api import cache

    monkeypatch.setenv("GITHUB_APP_ID", "42")
    github = MagicMock()
    github.__aenter__ = AsyncMock(return_value=github)
    github.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(GitHubClient, "as_app", MagicMock(return_value=github))
    checks = AsyncMock(return_value={RequiredCheck("Robin Approves")} if cached else set())
    monkeypatch.setattr(RequiredCheck, "for_branch", checks)
    branch = f"swr-{cached}-{refresh}"
    assert (
        await Repository.requires_approval_check("acme", "app", branch, include_any_source=True)
        is cached
    )
    monkeypatch.setattr(Repository, "resolve_default_branch", AsyncMock(return_value="main"))
    assert await Repository.requires_approval_check("acme", "app", branch) is False
    checks.return_value = (
        None if refresh is None else {RequiredCheck("Robin Approves")} if refresh else set()
    )
    now = cache._now_ms
    monkeypatch.setattr(cache, "_now_ms", lambda: now() + 2 * 60 * 60 * 1000)
    assert (
        await Repository.requires_approval_check("acme", "app", branch, include_any_source=True)
        is cached
    )
    await asyncio.gather(
        *(state.task for state in cache._SWR_STATES.values() if state.task),
        return_exceptions=True,
    )
    monkeypatch.setattr(cache, "_now_ms", now)
    assert await Repository.requires_approval_check(
        "acme", "app", branch, include_any_source=True
    ) is (cached if refresh is None else refresh)


@pytest.mark.parametrize("incoming_on_base", [False, True])
async def test_merging_base_does_not_disqualify_an_independent_reviewer(
    monkeypatch: pytest.MonkeyPatch, incoming_on_base: bool
) -> None:
    pull = _client(monkeypatch, "open", _ROBIN)
    pull.repo.can_write = AsyncMock(return_value=True)
    pull.repo.merge_base = AsyncMock(return_value="base-parent" if incoming_on_base else "common")
    pull.reviews.return_value = [_review("carol")]
    pull.commits = AsyncMock(
        return_value=[
            _commit("alice"),
            {
                **_commit("carol"),
                "parents": [{"sha": "feature"}, {"sha": "base-parent"}],
            },
        ]
    )
    pull.contribution_commits = PullRequestClient.contribution_commits.__get__(pull)
    approval = await PullRequestApproval.read(
        pull, {"user": _ROBIN, "head": {"sha": _HEAD}, "base": {"sha": "base"}}
    )
    assert approval.verdict()[0] is incoming_on_base
    assert "alice" in approval.excluded
    assert ("carol" in approval.excluded) is not incoming_on_base


@pytest.mark.parametrize("force_publish", [False, True])
async def test_no_check_is_posted_on_a_closed_pull_request(
    monkeypatch: pytest.MonkeyPatch, force_publish: bool
) -> None:
    pull = _client(monkeypatch, "closed")
    await PullRequestApproval.evaluate("acme", "app", 7, force_publish=force_publish)
    pull.repo.create_check_run.assert_not_awaited()


@pytest.mark.parametrize("access", ["allowed", "denied", "unlinked"])
async def test_manual_recheck_requires_the_callers_repository_access(
    monkeypatch: pytest.MonkeyPatch, access: str
) -> None:
    from fastapi import HTTPException

    tool = importlib.import_module("openswe.tools.recheck_pr_approval")
    monkeypatch.setattr(
        tool.RunConfig,
        "from_runtime",
        lambda: SimpleNamespace(
            mcp_caller=None, github_login="alice" if access != "unlinked" else None
        ),
    )
    authorize = AsyncMock(
        side_effect=HTTPException(403, "Access denied") if access == "denied" else None
    )
    monkeypatch.setattr(tool, "require_repo_access_for_user", authorize)
    evaluate = AsyncMock(return_value=(False, "Independent approval required"))
    monkeypatch.setattr(PullRequestApproval, "evaluate", evaluate)
    result = await tool.recheck_pr_approval.__wrapped__("https://github.com/acme/app/pull/7")
    if access == "allowed":
        authorize.assert_awaited_once_with("alice", "acme/app")
        evaluate.assert_awaited_once_with("acme", "app", 7, force_publish=True)
        assert result == {
            "success": True,
            "published": True,
            "passed": False,
            "summary": "Independent approval required",
        }
    else:
        assert result["success"] is False
        evaluate.assert_not_awaited()
