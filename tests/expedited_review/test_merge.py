"""PostgreSQL regressions for merging on recorded expedited review approvals."""

from typing import Any
from unittest.mock import AsyncMock

import httpx2
import pytest

from agent.expedited_review import lifecycle, merge, reviews
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
    """GitHub as merge sees it: submitted reviews stay approved until ``dismiss_as_stale``."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.reviews: list[tuple[str, str]] = []
        self.approved: set[int] = set()
        self.dismissed: list[str] = []
        self.dismiss_status = 200
        self.pull: dict[str, Any] = {"state": "open", "merged": False}
        self.merges: list[dict[str, Any]] = []
        self.merge_status = 200
        self.files = [_SOURCE, _TEST]
        self.readiness = _readiness()
        monkeypatch.setattr(merge, "repo_token", AsyncMock(return_value="app-token"))
        monkeypatch.setattr(merge, "_merge_token", AsyncMock(return_value="merge-token"))
        monkeypatch.setattr(merge, "assess_readiness", self._assess)
        monkeypatch.setattr(merge, "fetch_changed_files", self._files)
        monkeypatch.setattr(merge, "fetch_pr", self._pr)
        self.current_head: str | None = None
        monkeypatch.setattr(merge, "submit_approval", self._review)
        monkeypatch.setattr(merge, "github_request", self._request)
        monkeypatch.setattr(reviews, "github_request", self._request)
        monkeypatch.setattr(lifecycle, "repo_token", AsyncMock(return_value="app-token"))
        monkeypatch.setattr(lifecycle, "fetch_pr", self._pull)
        monkeypatch.setattr(lifecycle, "refresh_card", AsyncMock())
        monkeypatch.setattr(lifecycle, "add_slack_reaction", AsyncMock(return_value=True))
        self.comments: list[str] = []
        monkeypatch.setattr(merge, "post_github_comment", self._comment)

    async def _comment(
        self, repo_config: dict[str, str], issue_number: int, body: str, *, token: str
    ) -> bool:
        self.comments.append(body)
        return True

    def dismiss_as_stale(self) -> None:
        self.approved.clear()

    async def _assess(self, **_: object) -> Readiness:
        self.readiness.snapshot.approved_review_ids = frozenset(self.approved)
        return self.readiness

    async def _files(self, **_: object) -> list[ChangedFile]:
        return self.files

    async def _pr(self, **_: object) -> dict[str, Any]:
        return {"head": {"sha": self.current_head or self.readiness.snapshot.head_sha}}

    async def _pull(self, **_: object) -> dict[str, Any]:
        return self.pull

    async def _review(
        self, approval: ExpeditedApproval, vote: ApprovalVote, head_sha: str
    ) -> str | None:
        self.reviews.append((vote.github_login, head_sha))
        vote.github_review_id = 100 + len(self.reviews)
        vote.github_review_sha = head_sha
        self.approved.add(vote.github_review_id)
        return None

    async def _request(
        self, client: object, method: str, url: str, **kwargs: Any
    ) -> httpx2.Response:
        if url.endswith("/dismissals"):
            self.dismissed.append(url.rsplit("/", 2)[-2])
            return httpx2.Response(
                self.dismiss_status, json={}, request=httpx2.Request(method, url)
            )
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


async def _reviewed(approval: ExpeditedApproval, github: _GitHub) -> ExpeditedApproval:
    """Submit every vote's review as the click would have."""
    async with ExpeditedApproval.locked(approval.id) as (_, row):
        assert row is not None
        for vote in row.approvals:
            await github._review(row, vote, row.head_sha)
    return await _reload(approval)


async def test_merge_submits_a_missing_review_and_merges_pinned_to_the_head(
    github: _GitHub, open_approval: OpenApproval
) -> None:
    approval = await _approved(open_approval, "U_GRACE")

    result = await merge.merge_approved(approval)

    stored = await ExpeditedApproval.get(approval.id)
    assert stored is not None
    assert result.status == "merged"
    assert stored.state == "merged"
    assert github.reviews == [("grace", "abc123")]
    assert github.merges == [{"sha": "abc123", "merge_method": "squash"}]
    grace = next(vote for vote in stored.votes if vote.github_login == "grace")
    assert (grace.github_review_id, grace.github_review_sha) == (101, "abc123")
    assert github.dismissed == []


async def test_a_standing_review_from_the_click_is_not_submitted_again(
    github: _GitHub, open_approval: OpenApproval
) -> None:
    approval = await _reviewed(await _approved(open_approval, "U_GRACE", "U_LINUS"), github)
    github.files = [_SOURCE, ChangedFile(filename="tests/test_app.py", patch="+assert other")]
    github.readiness = _readiness("def456")

    result = await merge.merge_approved(approval)

    assert result.status == "merged"
    assert github.reviews == [("grace", "abc123"), ("linus", "abc123")]
    assert github.merges == [{"sha": "def456", "merge_method": "squash"}]


async def test_a_click_review_on_this_head_the_snapshot_missed_is_not_submitted_again(
    github: _GitHub, open_approval: OpenApproval
) -> None:
    approval = await _reviewed(await _approved(open_approval, "U_GRACE"), github)
    github.approved.clear()

    result = await merge.merge_approved(approval)

    assert result.status == "merged"
    assert github.reviews == [("grace", "abc123")]


async def test_a_review_github_dismissed_as_stale_is_submitted_on_the_new_head(
    github: _GitHub, open_approval: OpenApproval
) -> None:
    approval = await _reviewed(await _approved(open_approval, "U_GRACE"), github)
    github.files = [_SOURCE, ChangedFile(filename="tests/test_app.py", patch="+assert other")]
    github.readiness = _readiness("def456")
    github.dismiss_as_stale()

    result = await merge.merge_approved(approval)

    assert result.status == "merged"
    assert github.reviews == [("grace", "abc123"), ("grace", "def456")]


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


async def test_a_commit_changing_the_shown_diff_holds_the_merge_without_dismissing(
    github: _GitHub, open_approval: OpenApproval
) -> None:
    approval = await _reviewed(await _approved(open_approval, "U_GRACE", "U_LINUS"), github)
    github.files = [ChangedFile(filename="src/app.py", patch="+different"), _TEST]
    github.readiness = _readiness("def456")

    result = await merge.merge_approved(approval)

    stored = await _reload(approval)
    assert result.status == "diff_changed"
    assert stored.state == "open"
    assert github.dismissed == [] and github.merges == []


async def test_keeping_the_approval_across_a_diff_change_merges_and_explains_it(
    github: _GitHub, open_approval: OpenApproval
) -> None:
    approval = await _reviewed(await _approved(open_approval, "U_GRACE"), github)
    github.files = [ChangedFile(filename="src/app.py", additions=1, patch="+renamed"), _TEST]
    github.readiness = _readiness("def456")

    result = await merge.merge_approved(approval, "renamed a local variable for lint")

    assert result.status == "merged"
    assert github.dismissed == []
    assert github.merges == [{"sha": "def456", "merge_method": "squash"}]
    assert len(github.comments) == 1 and "renamed a local variable" in github.comments[0]


async def test_a_diff_grown_past_the_limit_discards_the_votes_even_when_kept(
    github: _GitHub, open_approval: OpenApproval
) -> None:
    approval = await _reviewed(await _approved(open_approval, "U_GRACE", "U_LINUS"), github)
    github.files = [
        ChangedFile(filename="src/app.py", additions=40, patch="+" * 40),
        _TEST,
    ]
    github.readiness = _readiness("def456")

    result = await merge.merge_approved(approval, "small follow-up")

    stored = await _reload(approval)
    assert result.status == "invalidated"
    assert stored.state == "superseded"
    assert sorted(github.dismissed) == ["101", "102"]
    assert github.merges == [] and github.comments == []


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
        row.state = "cancelled"

    result = await merge.merge_approved(approval)

    assert result.status == "closed"
    assert github.reviews == [] and github.merges == []


async def test_a_head_that_moves_after_the_checks_writes_nothing(
    github: _GitHub, open_approval: OpenApproval
) -> None:
    approval = await _approved(open_approval, "U_GRACE")
    github.current_head = "def456"

    result = await merge.merge_approved(approval)

    assert result.status == "not_ready"
    assert (await _reload(approval)).state == "open"
    assert github.reviews == [] and github.merges == []


async def test_someone_else_merging_on_github_only_marks_the_card_and_reacts(
    github: _GitHub, open_approval: OpenApproval, monkeypatch: pytest.MonkeyPatch
) -> None:
    approval = await _reviewed(await _approved(open_approval, "U_GRACE"), github)
    reactions = AsyncMock(return_value=True)
    posts = AsyncMock()
    wakes = AsyncMock()
    monkeypatch.setattr(lifecycle, "add_slack_reaction", reactions)
    monkeypatch.setattr(lifecycle, "post_slack_thread_reply_with_ts", posts)
    monkeypatch.setattr(lifecycle, "dispatch_agent_run", wakes)
    github.pull = {"state": "closed", "merged": True}

    await lifecycle.close_for_pull_request("lc", "repo", 7)
    await lifecycle.close_for_pull_request("lc", "repo", 7)

    assert (await _reload(approval)).state == "merged"
    reactions.assert_awaited_once_with("C1", "1.0", "merged")
    posts.assert_not_awaited()
    wakes.assert_not_awaited()
    assert github.dismissed == [] and github.merges == []


async def test_a_pr_closed_unmerged_closes_the_card_and_dismisses_its_reviews(
    github: _GitHub, open_approval: OpenApproval
) -> None:
    approval = await _reviewed(await _approved(open_approval, "U_GRACE"), github)
    github.pull = {"state": "closed", "merged": False}

    await lifecycle.close_for_pull_request("lc", "repo", 7)

    stored = await _reload(approval)
    assert stored.state == "cancelled"
    assert github.dismissed == ["101"]
    assert stored.votes[0].github_review_id is None


async def test_a_late_close_webhook_leaves_a_reopened_pr_card_open(
    github: _GitHub, open_approval: OpenApproval
) -> None:
    approval = await _reviewed(await _approved(open_approval, "U_GRACE"), github)

    await lifecycle.close_for_pull_request("lc", "repo", 7)

    assert (await _reload(approval)).state == "open"
    assert github.dismissed == []


async def test_a_dismissal_github_refused_is_retried_when_a_card_next_closes(
    github: _GitHub, open_approval: OpenApproval
) -> None:
    approval = await _reviewed(await _approved(open_approval, "U_GRACE"), github)
    github.dismiss_status = 500

    await lifecycle.retire(approval, "cancelled", "dismissed by <@U_LINUS>")
    refused = await _reload(approval)
    github.dismiss_status = 200
    await lifecycle.withdraw_reviews(refused)

    assert refused.votes[0].github_review_id == 101
    assert github.dismissed == ["101", "101"]
    assert (await _reload(approval)).votes[0].github_review_id is None
