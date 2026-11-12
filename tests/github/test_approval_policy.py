"""Who may approve a pull request Robin authored as itself (``Robin Approves``)."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openswe.github.approval_policy import PullRequestApproval
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequest
from openswe.github.repositories import Repository

_ROBIN = {"login": "robin-agent[bot]", "type": "Bot"}
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
        pytest.param(
            [_review("robin-agent[bot]", kind="Bot")], [], True, id="robin-approves-its-own"
        ),
    ],
)
def test_a_bot_authored_pr_needs_an_approval_from_outside_its_requester_and_pushers(
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
        commits=AsyncMock(return_value=[]),
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
    ],
)
async def test_the_check_is_posted_where_the_base_does_not_require_it(
    monkeypatch: pytest.MonkeyPatch, author: dict[str, str], conclusion: str
) -> None:
    monkeypatch.setattr(Repository, "requires_approval_check", AsyncMock(return_value=False))
    pull = _client(monkeypatch, "open", author)
    await PullRequestApproval.evaluate("acme", "app", 7)
    (check,), _ = pull.repo.create_check_run.call_args
    assert (check["name"], check["head_sha"], check["conclusion"]) == (
        "Robin Approves",
        _HEAD,
        conclusion,
    )
    assert pull.comment.await_count == (conclusion == "failure")


async def test_no_check_is_posted_on_a_closed_pull_request(monkeypatch: pytest.MonkeyPatch) -> None:
    pull = _client(monkeypatch, "closed")
    await PullRequestApproval.evaluate("acme", "app", 7)
    pull.repo.create_check_run.assert_not_awaited()
