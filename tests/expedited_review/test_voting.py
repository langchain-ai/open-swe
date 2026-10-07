"""PostgreSQL regressions for clicks on an expedited review card."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from openswe.expedited_review import voting
from openswe.human_review import lifecycle, people
from openswe.human_review.people import Outcome
from openswe.human_review.requests import HumanReviewParticipant, HumanReviewRequest
from openswe.slack import cards
from openswe.slack.http import SlackRequestError
from openswe.users import User
from tests.expedited_review.conftest import OpenApproval


class _Harness:
    def __init__(self) -> None:
        self.agent_prompts: list[str] = []
        self.marked_ready: list[str] = []
        self.wake_succeeds = True
        self.reviews: list[tuple[str, str]] = []
        self.review_error: str | None = None
        self.diff_unchanged = True

    async def notify_agent(self, approval: HumanReviewRequest, prompt: str) -> bool:
        self.agent_prompts.append(prompt)
        return self.wake_succeeds

    async def mark_ready(self, owner: str, repo: str, number: int, action: object, token: str):
        self.marked_ready.append(token)

    async def submit_approval(
        self, approval: HumanReviewRequest, vote: HumanReviewParticipant, head_sha: str
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
    monkeypatch.setattr(people, "repo_token", AsyncMock(return_value="app-token"))
    monkeypatch.setattr(people, "has_repo_write_permission", AsyncMock(return_value=True))

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
    ) -> str:
        self.broadcasts.append(reply_broadcast)
        return f"{2 + len(self.broadcasts)}.0"

    async def delete(self, channel_id: str, message_ts: str) -> bool:
        self.deleted.append(message_ts)
        return True


@pytest.fixture
def slack(monkeypatch: pytest.MonkeyPatch) -> _FakeSlack:
    fake = _FakeSlack()
    monkeypatch.setattr(
        lifecycle, "own_choices", AsyncMock(return_value=[{"id": "C1", "name": "eng"}])
    )
    monkeypatch.setattr(lifecycle, "delete_slack_message", fake.delete)
    monkeypatch.setattr(lifecycle, "get_slack_permalink", AsyncMock(return_value="https://t"))
    monkeypatch.setattr(lifecycle, "repo_token", AsyncMock(return_value=None))
    monkeypatch.setattr(cards, "post_slack_thread_reply_with_ts", fake.post)
    monkeypatch.setattr(cards, "delete_slack_message", fake.delete)
    return fake


async def _click(
    approval: HumanReviewRequest,
    slack_user: str,
    decision: voting.VoteAction = "approve",
) -> Outcome:
    current = await HumanReviewRequest.get(approval.id)
    assert current is not None
    return await voting.handle_vote(
        current,
        decision=decision,
        user=await User.for_person({"id": f"slack:{slack_user}", "platform": "slack"}),
    )


async def _stored(approval: HumanReviewRequest) -> HumanReviewRequest:
    stored = await HumanReviewRequest.get(approval.id)
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
    assert sorted((v.github_review_id, v.github_review_sha) for v in stored.participants) == [
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
    assert stored.participants[0].github_review_id is None


async def test_a_review_github_refuses_stays_recorded_for_the_merge(
    harness: _Harness, open_approval: OpenApproval
) -> None:
    approval = await open_approval()
    harness.review_error = "GitHub rejected @grace's review: 422 nope"

    outcome = await _click(approval, "U_GRACE")

    stored = await _stored(approval)
    assert "422 nope" in outcome.message and "when it merges" in outcome.message
    assert stored.approvers == ["grace"]
    assert stored.participants[0].github_review_id is None
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


async def test_readiness_button_is_delivered_only_to_the_author(
    open_approval: OpenApproval, monkeypatch: pytest.MonkeyPatch
) -> None:
    approval = await open_approval(awaiting_ready=True)
    private_messages: list[tuple[str, object]] = []

    async def deliver(user: str, text: str, *, blocks: object) -> tuple[str, str]:
        private_messages.append((user, blocks))
        return "D_ADA", "4.0"

    ephemeral = AsyncMock(return_value=True)
    monkeypatch.setattr("openswe.slack.client.post_slack_ephemeral_message", ephemeral)
    monkeypatch.setattr(lifecycle, "send_dm_with_location", deliver)
    monkeypatch.setattr(lifecycle, "repo_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(lifecycle, "_diff_image_id", AsyncMock(return_value=None))
    from openswe.expedited_review.eligibility import ChangedFile

    monkeypatch.setattr(
        lifecycle,
        "fetch_changed_files",
        AsyncMock(
            return_value=[
                ChangedFile(filename="agent/example.py", additions=1, deletions=0, patch="+fixed")
            ]
        ),
    )
    current = await _stored(approval)

    assert await lifecycle.prompt_author_ready(current) is None
    _, shared_blocks = await lifecycle.render(current, None)

    assert len(private_messages) == 1
    recipient, private_blocks = private_messages[0]
    assert recipient == "U_ADA"
    assert "+fixed" in str(private_blocks)
    ephemeral.assert_not_awaited()
    assert "open_swe_option_select_ready" in str(private_blocks)
    assert "open_swe_option_select_ready" not in str(shared_blocks)
    assert "open_swe_option_select_approve" not in str(shared_blocks)
    current.awaiting_ready = False
    _, ready_blocks = await lifecycle.render(current, None)
    assert "open_swe_option_select_approve" in str(ready_blocks)
    assert await lifecycle.prompt_author_ready(current) is None
    assert len(private_messages) == 1


async def test_draft_card_is_not_posted_until_ready(
    open_approval: OpenApproval, monkeypatch: pytest.MonkeyPatch
) -> None:
    approval = await open_approval(awaiting_ready=True)
    approval.slack_message_ts = ""
    approval.slack_dm_channel_id = "D_ADA"
    approval.slack_dm_message_ts = "4.0"
    approval = await approval.save()
    deleted = AsyncMock(return_value=True)
    monkeypatch.setattr(lifecycle, "delete_slack_message", deleted)
    monkeypatch.setattr(lifecycle, "note_for_concierge", AsyncMock())
    posted = AsyncMock(return_value="3.0")
    monkeypatch.setattr(lifecycle, "post_slack_thread_reply_with_ts", posted)
    monkeypatch.setattr(lifecycle, "_diff_image_id", AsyncMock(return_value=None))
    monkeypatch.setattr(lifecycle, "channel_choices", AsyncMock(return_value=[]))
    monkeypatch.setattr(lifecycle, "repo_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(lifecycle, "_files_for", AsyncMock(return_value=[]))

    with pytest.raises(SlackRequestError, match="draft card is author-only"):
        await lifecycle.post_card(approval, title="Fix", files=[])
    await lifecycle.refresh_card(approval)
    posted.assert_not_called()
    deleted.assert_not_awaited()
    approval.awaiting_ready = False
    await approval.save()
    await lifecycle.refresh_card(approval)

    stored = await _stored(approval)
    assert stored.slack_message_ts == "3.0"
    assert not stored.slack_dm_channel_id and not stored.slack_dm_message_ts
    deleted.assert_awaited_once_with("D_ADA", "4.0")
    assert "open_swe_option_select_approve" in str(posted.call_args)


async def test_author_only_prompt_delivery_failure_is_reported(
    open_approval: OpenApproval, monkeypatch: pytest.MonkeyPatch
) -> None:
    approval = await open_approval(awaiting_ready=True)
    monkeypatch.setattr(lifecycle, "send_dm_with_location", AsyncMock(return_value=None))
    monkeypatch.setattr(lifecycle, "repo_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(lifecycle, "fetch_changed_files", AsyncMock(return_value=[]))
    monkeypatch.setattr(lifecycle, "_diff_image_id", AsyncMock(return_value=None))

    problem = await lifecycle.prompt_author_ready(await _stored(approval))

    assert problem is not None and "mark it ready on GitHub" in problem
    assert (await _stored(approval)).awaiting_ready


async def test_a_fork_author_without_write_access_can_mark_their_draft_ready(
    harness: _Harness, open_approval: OpenApproval, monkeypatch: pytest.MonkeyPatch
) -> None:
    approval = await open_approval(awaiting_ready=True)
    monkeypatch.setattr(people, "has_repo_write_permission", AsyncMock(return_value=False))

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
    monkeypatch.setattr(people, "has_repo_write_permission", AsyncMock(return_value=False))
    read_only = await _click(approval, "U_LINUS")

    assert "not linked" in unlinked.message
    assert "no GitHub token" in tokenless.message
    assert "write access" in read_only.message
    assert (await _stored(approval)).participants == []


async def test_anyone_can_dismiss_the_card_without_waking_the_agent(
    harness: _Harness, open_approval: OpenApproval
) -> None:
    approval = await open_approval(awaiting_ready=True)

    first = await lifecycle.dismiss_request(await _stored(approval), "U_NOBODY")
    again = await lifecycle.dismiss_request(await _stored(approval), "U_GRACE")

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
    await lifecycle.dismiss_request(await _stored(approval), "U_GRACE")

    stored = await _stored(approval)
    assert sent.message == "Sent to the channel."
    assert "already sent to a channel" in again.message
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


class _OtherChannel:
    id = "C_OTHER"

    async def post(self, text: str, *, blocks: object = None, login: str | None = None) -> str:
        return "9.0"


async def test_a_copied_card_leaves_the_other_channel_once_it_closes_and_is_offered_again(
    harness: _Harness,
    open_approval: OpenApproval,
    slack: _FakeSlack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(voting, "sendable_channel", AsyncMock(return_value=_OtherChannel()))
    monkeypatch.setattr(voting, "still_internal", AsyncMock(return_value=True))
    approval = await open_approval()
    grace = await User.for_person({"id": "slack:U_GRACE", "platform": "slack"})

    sent = await voting.request_copy(await _stored(approval), "C_OTHER", grace)
    again = await voting.request_copy(await _stored(approval), "C_OTHER", grace)
    await lifecycle.dismiss_request(await _stored(approval), "U_GRACE")

    stored = await _stored(approval)
    assert sent.message == "Sent to <#C_OTHER>."
    assert "already sent to a channel" in again.message
    assert slack.deleted == ["9.0"]
    assert stored.slack_copy is None
    assert await HumanReviewRequest.copy_channels_for_author(
        "ADA", since=datetime.now(UTC) - timedelta(days=1)
    ) == ["C_OTHER"]


async def test_only_one_open_approval_per_pull_request(open_approval: OpenApproval) -> None:
    approval = await open_approval()

    duplicate = HumanReviewRequest(
        pull_request_id=approval.pull_request_id, head_sha="zzz", kind="standard"
    )
    with pytest.raises(IntegrityError):
        await duplicate.save()
    assert await HumanReviewRequest.active_for("lc", "repo", 7) is not None


async def test_broadcast_to_configured_review_channel(
    open_approval: OpenApproval, monkeypatch: pytest.MonkeyPatch
) -> None:
    approval = await open_approval()
    async with HumanReviewRequest.locked(approval.id) as (_, row):
        assert row is not None
        row.slack_channel_choices = [{"id": "C_REVIEW", "name": "review"}]
    approval = await _stored(approval)
    monkeypatch.setattr(voting, "still_internal", AsyncMock(return_value=True))
    monkeypatch.setattr(voting, "sendable_channel", AsyncMock(return_value=_OtherChannel()))
    monkeypatch.setattr(lifecycle, "render", AsyncMock(return_value=("card", [])))
    monkeypatch.setattr(lifecycle, "refresh_card", AsyncMock())

    sent = await voting.request_broadcast(approval)
    assert sent.message == "Sent to <#C_OTHER>."
    stored = await _stored(approval)
    assert stored.slack_channel_id == "C1"
    assert stored.slack_copy_channel_id == "C_OTHER"
    assert stored.slack_copy_ts == "9.0"
    assert stored.slack_broadcast is False
