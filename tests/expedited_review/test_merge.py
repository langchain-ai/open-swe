"""PostgreSQL regressions for merging on recorded expedited review approvals."""

from typing import Any
from unittest.mock import AsyncMock

import httpx2
import pytest

from agent.expedited_review import lifecycle, merge
from agent.expedited_review.approvals import ApprovalVote, ExpeditedApproval
from agent.expedited_review.eligibility import ChangedFile, diff_fingerprint
from agent.expedited_review.readiness import PullRequestSnapshot, Readiness
from agent.users import User
from tests.expedited_review.conftest import OpenApproval

_SOURCE = ChangedFile(filename="src/app.py", additions=1, patch="+fixed")
_TEST = ChangedFile(filename="tests/test_app.py", additions=1, patch="+assert fixed")


def _readiness(head_sha: str = "abc123", blockers: list[str] | None = None) -> Readiness:
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
    return Readiness(snapshot, blockers or [])


class _GitHub:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.reviews: list[tuple[str, str]] = []
        self.merges: list[dict[str, Any]] = []
        self.comments: list[str] = []
        self.merge_status = 200
        self.files = [_SOURCE, _TEST]
        self.readiness = _readiness()
        monkeypatch.setattr(merge, "repo_token", AsyncMock(return_value="app-token"))
        monkeypatch.setattr(merge, "_merge_token", AsyncMock(return_value="merge-token"))
        monkeypatch.setattr(merge, "assess_readiness", self._assess)
        monkeypatch.setattr(merge, "fetch_changed_files", self._files)
        monkeypatch.setattr(merge, "fetch_pr", self._pr)
        self.current_head: str | None = None
        monkeypatch.setattr(merge, "_submit_github_approval", self._review)
        monkeypatch.setattr(merge, "github_request", self._request)
        monkeypatch.setattr(
            merge, "get_slack_permalink", AsyncMock(return_value="https://slack.test/card")
        )
        monkeypatch.setattr(lifecycle, "refresh_card", AsyncMock())
        monkeypatch.setattr(lifecycle, "add_slack_reaction", AsyncMock(return_value=True))

    async def _assess(self, **_: object) -> Readiness:
        return self.readiness

    async def _files(self, **_: object) -> list[ChangedFile]:
        return self.files

    async def _pr(self, **_: object) -> dict[str, Any]:
        return {"head": {"sha": self.current_head or self.readiness.snapshot.head_sha}}

    async def _review(self, approval: ExpeditedApproval, login: str, head_sha: str) -> int:
        self.reviews.append((login, head_sha))
        return 100 + len(self.reviews)

    async def _request(
        self, client: object, method: str, url: str, **kwargs: Any
    ) -> httpx2.Response:
        if url.endswith("/comments"):
            self.comments.append(kwargs["json"]["body"])
            return httpx2.Response(201, json={}, request=httpx2.Request(method, url))
        self.merges.append(kwargs["json"])
        return httpx2.Response(
            self.merge_status,
            json={"message": "Required status check is expected"},
            request=httpx2.Request(method, url),
        )


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> _GitHub:
    return _GitHub(monkeypatch)


async def _reload(approval: ExpeditedApproval) -> ExpeditedApproval:
    stored = await ExpeditedApproval.get(approval.id)
    assert stored is not None
    return stored


async def _approve(approval: ExpeditedApproval, *slack_ids: str) -> ExpeditedApproval:
    async with ExpeditedApproval.locked(approval.id) as (_, row):
        assert row is not None
        for slack_id in slack_ids:
            user = await User.for_person({"id": f"slack:{slack_id}", "platform": "slack"})
            assert user is not None
            row.votes.append(ApprovalVote(voter_user_id=user.id, decision="approve"))
    return await _reload(approval)


async def _approved(open_approval: OpenApproval, *slack_ids: str) -> ExpeditedApproval:
    approval = await open_approval(fingerprint=diff_fingerprint([_SOURCE, _TEST]))
    return await _approve(approval, *slack_ids)


async def test_merge_submits_the_review_links_the_card_and_merges_pinned_to_the_head(
    github: _GitHub, open_approval: OpenApproval
) -> None:
    approval = await _approved(open_approval, "U_GRACE")

    result = await merge.merge_approved(approval)

    stored = await ExpeditedApproval.get(approval.id)
    assert stored is not None
    assert result.status == "merged"
    assert stored.state == "merged"
    assert github.reviews == [("grace", "abc123")]
    assert github.comments == [
        "Approved in Slack by @grace via [expedited review](https://slack.test/card)."
    ]
    assert github.merges == [{"sha": "abc123", "merge_method": "squash"}]
    grace = next(vote for vote in stored.votes if vote.github_login == "grace")
    assert (grace.github_review_id, grace.github_review_sha) == (101, "abc123")


async def test_a_draft_waiting_for_its_author_does_not_merge(
    github: _GitHub, open_approval: OpenApproval
) -> None:
    approval = await open_approval(
        fingerprint=diff_fingerprint([_SOURCE, _TEST]), awaiting_ready=True
    )

    result = await merge.merge_approved(approval)

    assert result.status == "needs_approvals"
    assert github.reviews == [] and github.merges == []


async def test_a_commit_touching_only_unshown_tests_keeps_the_votes(
    github: _GitHub, open_approval: OpenApproval
) -> None:
    approval = await _approved(open_approval, "U_GRACE", "U_LINUS")
    github.files = [_SOURCE, ChangedFile(filename="tests/test_app.py", patch="+assert other")]
    github.readiness = _readiness("def456")

    result = await merge.merge_approved(approval)

    assert result.status == "merged"
    assert github.reviews == [("grace", "def456"), ("linus", "def456")]
    assert github.merges == [{"sha": "def456", "merge_method": "squash"}]


async def test_a_commit_changing_the_shown_diff_discards_the_votes(
    github: _GitHub, open_approval: OpenApproval
) -> None:
    approval = await _approved(open_approval, "U_GRACE", "U_LINUS")
    github.files = [ChangedFile(filename="src/app.py", patch="+different"), _TEST]
    github.readiness = _readiness("def456")

    result = await merge.merge_approved(approval)

    stored = await ExpeditedApproval.get(approval.id)
    assert stored is not None
    assert result.status == "invalidated"
    assert stored.state == "superseded"
    assert github.reviews == [] and github.merges == []


async def test_nothing_reaches_github_without_an_approval_or_while_blocked(
    github: _GitHub, open_approval: OpenApproval
) -> None:
    approval = await _approved(open_approval)
    short = await merge.merge_approved(approval)

    github.readiness = _readiness(blockers=["checks are still running"])
    blocked = await merge.merge_approved(await _approve(approval, "U_GRACE"))

    stored = await _reload(approval)
    assert short.status == "needs_approvals"
    assert blocked.status == "not_ready" and "checks are still running" in blocked.message
    assert stored.state == "open"
    assert github.reviews == [] and github.merges == []


async def test_a_refused_merge_keeps_the_card_open_and_does_not_resubmit_reviews(
    github: _GitHub, open_approval: OpenApproval
) -> None:
    approval = await _approved(open_approval, "U_GRACE", "U_LINUS")
    github.merge_status = 405

    refused = await merge.merge_approved(approval)
    github.merge_status = 200
    retried = await merge.merge_approved(await _reload(approval))

    assert refused.status == "refused" and "Required status check" in refused.message
    assert retried.status == "merged"
    assert github.reviews == [("grace", "abc123"), ("linus", "abc123")]
    assert len(github.comments) == 1
    assert len(github.merges) == 2


async def test_an_authors_own_vote_is_not_an_approval(
    github: _GitHub, open_approval: OpenApproval
) -> None:
    approval = await _approved(open_approval, "U_ADA")

    result = await merge.merge_approved(approval)

    assert result.status == "needs_approvals"
    assert github.reviews == [] and github.merges == []


async def test_a_card_closed_before_the_lock_is_taken_writes_nothing(
    github: _GitHub, open_approval: OpenApproval
) -> None:
    approval = await _approved(open_approval, "U_GRACE")
    async with ExpeditedApproval.locked(approval.id) as (_, row):
        assert row is not None
        row.state = "rejected"

    result = await merge.merge_approved(approval)

    assert result.status == "closed"
    assert github.reviews == [] and github.merges == [] and github.comments == []


async def test_a_head_that_moves_after_the_checks_writes_nothing(
    github: _GitHub, open_approval: OpenApproval
) -> None:
    approval = await _approved(open_approval, "U_GRACE")
    github.current_head = "def456"

    result = await merge.merge_approved(approval)

    assert result.status == "not_ready"
    assert (await _reload(approval)).state == "open"
    assert github.reviews == [] and github.merges == []
