"""PostgreSQL regressions for clicks on an expedited review card."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from agent.expedited_review import lifecycle, voting
from agent.expedited_review.approvals import ApprovalVote, ExpeditedApproval
from agent.users import User
from tests.expedited_review.conftest import OpenApproval


class _Harness:
    def __init__(self) -> None:
        self.agent_prompts: list[str] = []
        self.marked_ready: list[str] = []
        self.wake_succeeds = True
        self.reviews: list[tuple[str, str]] = []
        self.review_error: str | None = None
        self.diff_unchanged = True

    async def notify_agent(self, approval: ExpeditedApproval, prompt: str) -> bool:
        self.agent_prompts.append(prompt)
        return self.wake_succeeds

    async def mark_ready(self, owner: str, repo: str, number: int, action: object, token: str):
        self.marked_ready.append(token)

    async def submit_approval(
        self, approval: ExpeditedApproval, vote: ApprovalVote, head_sha: str
    ) -> str | None:
        if self.review_error is not None:
            return self.review_error
        self.reviews.append((vote.github_login, head_sha))
        vote.github_review_id = 100 + len(self.reviews)
        vote.github_review_sha = head_sha
        return None


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> _Harness:
    h = _Harness()
    monkeypatch.setattr(voting, "repo_token", AsyncMock(return_value="app-token"))
    monkeypatch.setattr(voting, "has_repo_write_permission", AsyncMock(return_value=True))

    async def user_token(login: str) -> str:
        return f"token-{login}"

    monkeypatch.setattr(voting, "get_valid_access_token", user_token)
    monkeypatch.setattr(voting, "act_on_pull_request", h.mark_ready)
    monkeypatch.setattr(voting, "refresh_card", AsyncMock())
    monkeypatch.setattr(voting, "notify_agent", h.notify_agent)
    monkeypatch.setattr(voting, "fetch_pr", AsyncMock(return_value={"head": {"sha": "def456"}}))
    monkeypatch.setattr(voting, "fetch_changed_files", AsyncMock(return_value=[]))
    monkeypatch.setattr(voting, "fingerprint_matches", lambda files, fp: h.diff_unchanged)
    monkeypatch.setattr(voting, "submit_approval", h.submit_approval)
    monkeypatch.setattr(lifecycle, "refresh_card", AsyncMock())
    monkeypatch.setattr(lifecycle, "notify_agent", h.notify_agent)
    monkeypatch.setattr(lifecycle, "repo_token", AsyncMock(return_value=None))
    return h


class _FakeSlack:
    def __init__(self) -> None:
        self.broadcasts: list[bool] = []
        self.deleted: list[str] = []

    async def post(
        self, channel_id: str, thread_ts: str, text: str, *, reply_broadcast: bool, **_: object
    ) -> tuple[str, None]:
        self.broadcasts.append(reply_broadcast)
        return f"{2 + len(self.broadcasts)}.0", None

    async def delete(self, channel_id: str, message_ts: str) -> bool:
        self.deleted.append(message_ts)
        return True


@pytest.fixture
def slack(monkeypatch: pytest.MonkeyPatch) -> _FakeSlack:
    fake = _FakeSlack()
    monkeypatch.setattr(lifecycle, "_broadcast_channel", AsyncMock(return_value="#eng"))
    monkeypatch.setattr(lifecycle, "repo_token", AsyncMock(return_value=None))
    monkeypatch.setattr(lifecycle, "post_slack_thread_reply_with_ts", fake.post)
    monkeypatch.setattr(lifecycle, "delete_slack_message", fake.delete)
    return fake


async def _click(
    approval: ExpeditedApproval,
    slack_user: str,
    decision: voting.VoteAction = "approve",
) -> voting.VoteOutcome:
    current = await ExpeditedApproval.get(approval.id)
    assert current is not None
    return await voting.handle_vote(
        current,
        decision=decision,
        user=await User.for_person({"id": f"slack:{slack_user}", "platform": "slack"}),
    )


async def _stored(approval: ExpeditedApproval) -> ExpeditedApproval:
    stored = await ExpeditedApproval.get(approval.id)
    assert stored is not None
    return stored


async def test_each_approval_reaches_github_on_click_and_wakes_the_agent_once(
    harness: _Harness, open_approval: OpenApproval
) -> None:
    approval = await open_approval()

    grace = await _click(approval, "U_GRACE")
    await _click(approval, "U_LINUS")

    stored = await _stored(approval)
    assert stored.state == "open"
    assert sorted(stored.approvers) == ["grace", "linus"]
    assert grace.message == ""
    assert harness.reviews == [("grace", "def456"), ("linus", "def456")]
    assert sorted((v.github_review_id, v.github_review_sha) for v in stored.votes) == [
        (101, "def456"),
        (102, "def456"),
    ]
    assert len(harness.agent_prompts) == 1
    assert "@grace" in harness.agent_prompts[0]


async def test_a_click_on_a_card_whose_diff_changed_records_the_vote_without_a_review(
    harness: _Harness, open_approval: OpenApproval
) -> None:
    approval = await open_approval()
    harness.diff_unchanged = False

    outcome = await _click(approval, "U_GRACE")

    stored = await _stored(approval)
    assert "changed the diff" in outcome.message
    assert stored.approvers == ["grace"]
    assert harness.reviews == []
    assert stored.votes[0].github_review_id is None


async def test_a_review_github_refuses_stays_recorded_for_the_merge(
    harness: _Harness, open_approval: OpenApproval
) -> None:
    approval = await open_approval()
    harness.review_error = "GitHub rejected @grace's review: 422 nope"

    outcome = await _click(approval, "U_GRACE")

    stored = await _stored(approval)
    assert "422 nope" in outcome.message and "when it merges" in outcome.message
    assert stored.approvers == ["grace"]
    assert stored.votes[0].github_review_id is None
    assert len(harness.agent_prompts) == 1


async def test_the_author_cannot_approve_their_own_pull_request(
    harness: _Harness, open_approval: OpenApproval
) -> None:
    approval = await open_approval()

    outcome = await _click(approval, "U_ADA")

    assert "someone else" in outcome.message
    assert not (await _stored(approval)).approved
    assert harness.agent_prompts == []


async def test_a_draft_waits_for_its_author_to_mark_it_ready(
    harness: _Harness, open_approval: OpenApproval
) -> None:
    approval = await open_approval(awaiting_ready=True)

    early = await _click(approval, "U_GRACE")
    stranger = await _click(approval, "U_GRACE", decision="ready")
    ready = await _click(approval, "U_ADA", decision="ready")
    await _click(approval, "U_GRACE")

    stored = await _stored(approval)
    assert "mark this draft ready" in early.message
    assert "Only the pull request's author" in stranger.message
    assert "Marked ready" in ready.message
    assert harness.marked_ready == ["token-ada"]
    assert not stored.awaiting_ready
    assert stored.approvers == ["grace"]


async def test_a_fork_author_without_write_access_can_mark_their_draft_ready(
    harness: _Harness, open_approval: OpenApproval, monkeypatch: pytest.MonkeyPatch
) -> None:
    approval = await open_approval(awaiting_ready=True)
    monkeypatch.setattr(voting, "has_repo_write_permission", AsyncMock(return_value=False))

    outcome = await _click(approval, "U_ADA", decision="ready")

    assert "Marked ready" in outcome.message
    assert harness.marked_ready == ["token-ada"]


async def test_an_approval_whose_wake_up_fails_tells_the_voter(
    harness: _Harness, open_approval: OpenApproval
) -> None:
    approval = await open_approval()
    harness.wake_succeeds = False

    outcome = await _click(approval, "U_GRACE")

    assert "tag it in the thread" in outcome.message
    assert (await _stored(approval)).approved


async def test_github_refusing_to_undraft_keeps_the_card_waiting(
    harness: _Harness, open_approval: OpenApproval, monkeypatch: pytest.MonkeyPatch
) -> None:
    approval = await open_approval(awaiting_ready=True)
    monkeypatch.setattr(
        voting, "act_on_pull_request", AsyncMock(side_effect=HTTPException(422, "nope"))
    )

    outcome = await _click(approval, "U_ADA", decision="ready")

    assert "nope" in outcome.message
    assert (await _stored(approval)).awaiting_ready


async def test_one_person_cannot_approve_twice(
    harness: _Harness, open_approval: OpenApproval
) -> None:
    approval = await open_approval()

    await _click(approval, "U_GRACE")
    outcome = await _click(approval, "U_GRACE")

    assert "already" in outcome.message
    assert (await _stored(approval)).approvers == ["grace"]


async def test_unlinked_read_only_or_tokenless_users_cannot_vote(
    harness: _Harness, open_approval: OpenApproval, monkeypatch: pytest.MonkeyPatch
) -> None:
    approval = await open_approval()

    unlinked = await _click(approval, "U_NOBODY")
    monkeypatch.setattr(voting, "get_valid_access_token", AsyncMock(return_value=None))
    tokenless = await _click(approval, "U_GRACE")
    monkeypatch.setattr(voting, "has_repo_write_permission", AsyncMock(return_value=False))
    read_only = await _click(approval, "U_LINUS")

    assert "not linked" in unlinked.message
    assert "no GitHub token" in tokenless.message
    assert "write access" in read_only.message
    assert (await _stored(approval)).votes == []


async def test_anyone_can_dismiss_the_card_without_waking_the_agent(
    harness: _Harness, open_approval: OpenApproval
) -> None:
    approval = await open_approval(awaiting_ready=True)

    first = await voting.dismiss(await _stored(approval), "U_NOBODY")
    again = await voting.dismiss(await _stored(approval), "U_GRACE")

    stored = await _stored(approval)
    assert first.message == "Dismissed."
    assert "already closed" in again.message
    assert stored.state == "cancelled"
    assert stored.detail == "dismissed by <@U_NOBODY>"
    assert harness.agent_prompts == []


async def test_a_broadcast_card_leaves_the_channel_once_it_closes(
    harness: _Harness, open_approval: OpenApproval, slack: _FakeSlack
) -> None:
    approval = await open_approval()

    sent = await voting.request_broadcast(await _stored(approval))
    again = await voting.request_broadcast(await _stored(approval))
    await voting.dismiss(await _stored(approval), "U_GRACE")

    stored = await _stored(approval)
    assert sent.message == "Sent to the channel."
    assert "already in the channel" in again.message
    assert slack.broadcasts == [True, False]
    assert slack.deleted == ["2.0", "3.0"]
    assert stored.state == "cancelled"
    assert not stored.slack_broadcast
    assert stored.slack_message_ts == "4.0"


async def test_a_broadcast_card_leaves_the_channel_once_it_is_approved(
    harness: _Harness, open_approval: OpenApproval, slack: _FakeSlack
) -> None:
    approval = await open_approval()

    await voting.request_broadcast(await _stored(approval))
    await _click(approval, "U_GRACE")

    stored = await _stored(approval)
    assert slack.broadcasts == [True, False]
    assert slack.deleted == ["2.0", "3.0"]
    assert stored.state == "open"
    assert stored.approved
    assert not stored.slack_broadcast
    assert stored.slack_message_ts == "4.0"


async def test_only_one_open_approval_per_pull_request(open_approval: OpenApproval) -> None:
    approval = await open_approval()

    duplicate = ExpeditedApproval(pull_request_id=approval.pull_request_id, head_sha="zzz")
    with pytest.raises(IntegrityError):
        await duplicate.save()
    assert await ExpeditedApproval.active_for("lc", "repo", 7) is not None
