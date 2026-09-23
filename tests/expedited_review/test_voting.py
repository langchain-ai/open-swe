"""PostgreSQL regressions for expedited approval voting and merging."""

from typing import Any
from unittest.mock import AsyncMock

import httpx2
import pytest
from sqlalchemy.exc import IntegrityError

from agent.expedited_review import voting, watch
from agent.expedited_review.approvals import ExpeditedApproval
from agent.expedited_review.readiness import PullRequestSnapshot, Readiness
from agent.github.pull_requests import PullRequest
from agent.users import User

pytestmark = pytest.mark.usefixtures("registry_db")

_PEOPLE = {"U_ADA": ("ada", "1"), "U_GRACE": ("grace", "2"), "U_LINUS": ("linus", "3")}


@pytest.fixture(autouse=True)
def _authorized_logins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", ",".join(login for login, _ in _PEOPLE.values()))


async def _register_people() -> None:
    for slack_id, (login, github_id) in _PEOPLE.items():
        user = await User.sign_in("github", github_id, login=login)
        await user.link("slack", slack_id, team_id="T1")


def _ready(head_sha: str = "abc123") -> Readiness:
    snapshot = PullRequestSnapshot(
        state="open",
        merged=False,
        draft=False,
        head_sha=head_sha,
        title="Fix typo",
        author="ada",
        mergeable=True,
        mergeable_state="clean",
        check_state="success",
        unresolved_threads=0,
        allowed_merge_methods=["squash"],
    )
    return Readiness(snapshot, [])


class _Harness:
    def __init__(self) -> None:
        self.github_reviews: list[str] = []
        self.merge_calls: list[dict[str, Any]] = []
        self.agent_prompts: list[str] = []
        self.merge_status = 200

    async def submit_review(self, approval: ExpeditedApproval, login: str) -> int:
        self.github_reviews.append(login)
        return 100 + len(self.github_reviews)

    async def github_request(
        self, client: object, method: str, url: str, **kwargs: Any
    ) -> httpx2.Response:
        self.merge_calls.append({"method": method, "url": url, **kwargs})
        return httpx2.Response(
            self.merge_status,
            json={"merged": self.merge_status == 200, "message": "Refused"},
            request=httpx2.Request(method, url),
        )

    async def notify_agent(self, approval: ExpeditedApproval, prompt: str) -> None:
        self.agent_prompts.append(prompt)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> _Harness:
    h = _Harness()
    monkeypatch.setattr(voting, "repo_token", AsyncMock(return_value="app-token"))
    monkeypatch.setattr(voting, "has_repo_write_permission", AsyncMock(return_value=True))
    monkeypatch.setattr(voting, "_submit_github_approval", h.submit_review)
    monkeypatch.setattr(voting, "assess_readiness", AsyncMock(return_value=_ready()))
    monkeypatch.setattr(voting, "_merge_token", AsyncMock(return_value="merge-token"))
    monkeypatch.setattr(voting, "github_request", h.github_request)
    monkeypatch.setattr(voting, "refresh_card", AsyncMock())
    monkeypatch.setattr(watch, "refresh_card", AsyncMock())
    monkeypatch.setattr(watch, "notify_agent", h.notify_agent)
    monkeypatch.setattr(watch, "_delete_cron", AsyncMock())
    monkeypatch.setattr(watch, "add_slack_reaction", AsyncMock(return_value=True))
    return h


async def _open_approval() -> ExpeditedApproval:
    await _register_people()
    pr = await PullRequest(owner="lc", repo="repo", number=7, author="ada").save()
    assert pr.author_user_id is not None
    approval = ExpeditedApproval(
        pull_request_id=pr.id,
        head_sha="abc123",
        thread_id="thread-1",
        state="open",
        slack_channel_id="C1",
        slack_thread_ts="1.0",
        slack_message_ts="2.0",
    )
    return await approval.save()


async def _vote(approval: ExpeditedApproval, slack_user: str, decision: str = "approve"):
    current = await ExpeditedApproval.get(approval.id)
    assert current is not None
    return await voting.handle_vote(
        current,
        decision="approve" if decision == "approve" else "reject",
        user=await User.for_person({"id": f"slack:{slack_user}", "platform": "slack"}),
    )


async def test_two_distinct_approvals_merge_pinned_to_the_reviewed_sha(harness: _Harness) -> None:
    approval = await _open_approval()

    first = await _vote(approval, "U_GRACE")
    second = await _vote(approval, "U_LINUS")

    stored = await ExpeditedApproval.get(approval.id)
    assert stored is not None
    assert first.private and not second.private
    assert stored.state == "merged"
    assert sorted(stored.approvers) == ["grace", "linus"]
    assert harness.github_reviews == ["grace", "linus"]
    assert len(harness.merge_calls) == 1
    assert harness.merge_calls[0]["json"] == {"sha": "abc123", "merge_method": "squash"}


async def test_the_author_counts_without_a_github_review(harness: _Harness) -> None:
    approval = await _open_approval()

    await _vote(approval, "U_ADA")
    await _vote(approval, "U_GRACE")

    stored = await ExpeditedApproval.get(approval.id)
    assert stored is not None
    assert stored.state == "merged"
    assert harness.github_reviews == ["grace"]
    ada = next(vote for vote in stored.votes if vote.github_login == "ada")
    assert ada.github_review_id is None
    assert ada.voter_user_id == stored.pull_request.author_user_id


async def test_one_person_cannot_approve_twice(harness: _Harness) -> None:
    approval = await _open_approval()

    await _vote(approval, "U_GRACE")
    outcome = await _vote(approval, "U_GRACE")

    stored = await ExpeditedApproval.get(approval.id)
    assert stored is not None
    assert "already" in outcome.message
    assert stored.state == "open"
    assert stored.approvers == ["grace"]
    assert harness.merge_calls == []


async def test_unlinked_or_read_only_users_cannot_vote(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    approval = await _open_approval()

    unlinked = await _vote(approval, "U_NOBODY")
    monkeypatch.setattr(voting, "has_repo_write_permission", AsyncMock(return_value=False))
    read_only = await _vote(approval, "U_GRACE")

    stored = await ExpeditedApproval.get(approval.id)
    assert stored is not None
    assert "not linked" in unlinked.message
    assert "write access" in read_only.message
    assert stored.votes == []


async def test_rejection_ends_the_vote_and_tells_the_agent_once(harness: _Harness) -> None:
    approval = await _open_approval()
    await _vote(approval, "U_GRACE")

    await _vote(approval, "U_LINUS", decision="reject")
    late = await _vote(approval, "U_ADA")

    stored = await ExpeditedApproval.get(approval.id)
    assert stored is not None
    assert stored.state == "rejected"
    assert stored.rejection is not None and stored.rejection.github_login == "linus"
    assert "no longer accepting" in late.message
    assert len(harness.agent_prompts) == 1
    assert "linus" in harness.agent_prompts[0]
    assert harness.merge_calls == []


async def test_github_refusing_the_merge_fails_the_approval_without_bypass(
    harness: _Harness,
) -> None:
    approval = await _open_approval()
    harness.merge_status = 405

    await _vote(approval, "U_GRACE")
    await _vote(approval, "U_LINUS")

    stored = await ExpeditedApproval.get(approval.id)
    assert stored is not None
    assert stored.state == "failed"
    assert "Refused" in stored.detail
    assert len(harness.merge_calls) == 1
    assert len(harness.agent_prompts) == 1


async def test_a_new_commit_before_quorum_discards_votes(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    approval = await _open_approval()
    await _vote(approval, "U_GRACE")
    monkeypatch.setattr(voting, "assess_readiness", AsyncMock(return_value=_ready("def456")))

    await _vote(approval, "U_LINUS")

    stored = await ExpeditedApproval.get(approval.id)
    assert stored is not None
    assert stored.state == "superseded"
    assert harness.merge_calls == []


async def test_only_one_active_approval_per_pull_request() -> None:
    approval = await _open_approval()

    duplicate = ExpeditedApproval(pull_request_id=approval.pull_request_id, head_sha="zzz")
    with pytest.raises(IntegrityError):
        await duplicate.save()
    assert await ExpeditedApproval.active_for("lc", "repo", 7) is not None
