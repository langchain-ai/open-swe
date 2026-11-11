"""Who may approve a pull request Robin authored as itself (``Robin Approves``)."""

import pytest

from openswe.github.approval_policy import PullRequestApproval

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
