"""PostgreSQL regressions for clicks on an expedited review card."""

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from openswe.dashboard import profiles
from openswe.expedited_review import voting
from openswe.expedited_review.eligibility import ChangedFile
from openswe.github import http as github_http
from openswe.github.http import RepoClient
from openswe.github.pull_request_status import PullRequestClient
from openswe.human_review import lifecycle
from openswe.human_review.lifecycle import ReviewCard
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

    async def notify_agent(self, prompt: str) -> bool:
        self.agent_prompts.append(prompt)
        return self.wake_succeeds

    async def mark_ready(self, pull: PullRequestClient, action: object) -> None:
        self.marked_ready.append(
            pull.repo.github.http.headers["Authorization"].removeprefix("Bearer ")
        )

    async def submit_approval(
        self, approval: HumanReviewRequest, vote: HumanReviewParticipant, head_sha: str
    ) -> str | None:
        if self.review_error is not None:
            return self.review_error
        self.reviews.append((vote.github_login, head_sha))
        vote.github_review_id = 100 + len(self.reviews)
        vote.github_review_sha = head_sha
        return None


def _json(request: httpx2.Request, payload: object, status: int = 200) -> httpx2.Response:
    return httpx2.Response(status, json=payload, request=request)


async def _fake_github(
    _client: httpx2.AsyncClient, method: str, url: str, **_kwargs: object
) -> httpx2.Response:
    """GitHub as a card flow reads it: an open PR on ``def456`` with no files, reviews or config."""
    request = httpx2.Request(method, url)
    path = request.url.path
    if path.endswith("/permission"):
        return _json(request, {"permission": "write"})
    if path.endswith(("/files", "/reviews")):
        return _json(request, [])
    if re.search(r"/pulls/\d+$", path):
        return _json(request, {"state": "open", "head": {"sha": "def456"}})
    return _json(request, {"message": "Not Found"}, 404)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, github_app: AsyncMock) -> _Harness:
    h = _Harness()
    monkeypatch.setattr(github_http, "github_request", _fake_github)

    async def user_token(login: str) -> str:
        return f"token-{login}"

    monkeypatch.setattr(voting, "get_valid_access_token", user_token)
    monkeypatch.setattr(profiles, "get_valid_access_token", user_token)
    monkeypatch.setattr(voting, "act_on_pull_request", h.mark_ready)
    monkeypatch.setattr(ReviewCard, "refresh", AsyncMock())
    monkeypatch.setattr(ReviewCard, "notify_agent", h.notify_agent)
    monkeypatch.setattr(voting, "fingerprint_matches", lambda files, fp: h.diff_unchanged)
    monkeypatch.setattr(voting, "submit_approval", h.submit_approval)
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
    open_approval: OpenApproval, monkeypatch: pytest.MonkeyPatch, github_app: AsyncMock
) -> None:
    approval = await open_approval(awaiting_ready=True)
    private_messages: list[tuple[str, object]] = []

    async def deliver(
        user: str, text: str, *, blocks: object, origin: object = None
    ) -> tuple[str, str]:
        private_messages.append((user, blocks))
        return "D_ADA", "4.0"

    ephemeral = AsyncMock(return_value=True)
    monkeypatch.setattr("openswe.slack.client.post_slack_ephemeral_message", ephemeral)
    monkeypatch.setattr(lifecycle, "send_dm_with_location", deliver)
    monkeypatch.setattr(
        lifecycle, "upload_slack_thread_file", AsyncMock(side_effect=SlackRequestError("down"))
    )
    monkeypatch.setattr(
        ChangedFile,
        "of_pull",
        AsyncMock(
            return_value=[
                ChangedFile(filename="agent/example.py", additions=1, deletions=0, patch="+fixed")
            ]
        ),
    )
    current = await _stored(approval)

    assert await ReviewCard(current).prompt_author_ready() is None
    _, shared_blocks = await ReviewCard(current).render(None)

    assert len(private_messages) == 1
    recipient, private_blocks = private_messages[0]
    assert recipient == "U_ADA"
    assert "+fixed" in str(private_blocks)
    ephemeral.assert_not_awaited()
    assert "open_swe_option_select_ready" in str(private_blocks)
    assert "open_swe_option_select_ready" not in str(shared_blocks)
    assert "open_swe_option_select_approve" not in str(shared_blocks)
    current.awaiting_ready = False
    _, ready_blocks = await ReviewCard(current).render(None)
    assert "open_swe_option_select_approve" in str(ready_blocks)
    assert await ReviewCard(current).prompt_author_ready() is None
    assert len(private_messages) == 1


async def test_draft_card_is_not_posted_until_ready(
    open_approval: OpenApproval, monkeypatch: pytest.MonkeyPatch, github_app: AsyncMock
) -> None:
    approval = await open_approval(awaiting_ready=True)
    approval.slack_message_ts = ""
    approval.slack_dm_channel_id = "D_ADA"
    approval.slack_dm_message_ts = "4.0"
    approval = await approval.save()
    deleted = AsyncMock(return_value=True)
    monkeypatch.setattr(lifecycle, "delete_slack_message", deleted)
    notes = AsyncMock()
    monkeypatch.setattr(lifecycle, "note_for_concierge", notes)
    updated = AsyncMock()
    monkeypatch.setattr(lifecycle, "update_slack_message", updated)
    monkeypatch.setattr(lifecycle, "get_slack_permalink", AsyncMock(return_value="https://origin"))
    posted = AsyncMock(return_value="3.0")
    monkeypatch.setattr(lifecycle, "post_slack_thread_reply_with_ts", posted)
    monkeypatch.setattr(lifecycle, "channel_choices", AsyncMock(return_value=[]))
    monkeypatch.setattr(ChangedFile, "of_pull", AsyncMock(return_value=[]))

    with pytest.raises(SlackRequestError, match="draft card is author-only"):
        await ReviewCard(approval).post_expedited(title="Fix", files=[])
    await ReviewCard(approval).refresh()
    posted.assert_not_called()
    deleted.assert_not_awaited()
    approval.awaiting_ready = False
    await approval.save()
    await ReviewCard(approval).refresh()

    stored = await _stored(approval)
    assert stored.slack_message_ts == "3.0"
    assert (stored.slack_dm_channel_id, stored.slack_dm_message_ts) == ("D_ADA", "4.0")
    deleted.assert_not_awaited()
    assert "Ready for review" in str(updated.call_args)
    assert "https://origin" in str(updated.call_args)
    assert "actions" not in str(updated.call_args)
    assert await ReviewCard(await _stored(approval)).refresh_author_dm(None)
    assert await ReviewCard(approval).refresh_author_dm(None)
    assert updated.await_count == notes.await_count == 1
    approval.state = "cancelled"
    approval.detail = "dismissed by <@U_ADA>"
    await approval.save()
    await ReviewCard(approval).refresh()
    await ReviewCard(approval).prompt_author_ready()
    assert notes.await_count == 2
    assert sum(call.args[0] == "D_ADA" for call in updated.await_args_list) == 2
    dm_updates = [call for call in updated.await_args_list if call.args[0] == "D_ADA"]
    assert "dismissed by" in str(dm_updates[-1])
    assert "actions" not in str(dm_updates[-1])
    deleted.assert_not_awaited()
    assert "open_swe_option_select_approve" in str(posted.call_args)


async def test_author_only_prompt_delivery_failure_is_reported(
    open_approval: OpenApproval, monkeypatch: pytest.MonkeyPatch, github_app: AsyncMock
) -> None:
    approval = await open_approval(awaiting_ready=True)
    monkeypatch.setattr(lifecycle, "send_dm_with_location", AsyncMock(return_value=None))
    monkeypatch.setattr(ChangedFile, "of_pull", AsyncMock(return_value=[]))

    problem = await ReviewCard(await _stored(approval)).prompt_author_ready()

    assert problem is not None and "mark it ready on GitHub" in problem
    assert (await _stored(approval)).awaiting_ready


async def test_a_fork_author_without_write_access_can_mark_their_draft_ready(
    harness: _Harness, open_approval: OpenApproval, monkeypatch: pytest.MonkeyPatch
) -> None:
    approval = await open_approval(awaiting_ready=True)
    monkeypatch.setattr(RepoClient, "can_write", AsyncMock(return_value=False))

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
    monkeypatch.setattr(RepoClient, "can_write", AsyncMock(return_value=False))
    read_only = await _click(approval, "U_LINUS")

    assert "not linked" in unlinked.message
    assert "no GitHub token" in tokenless.message
    assert "write access" in read_only.message
    assert (await _stored(approval)).participants == []


async def test_anyone_can_dismiss_the_card_without_waking_the_agent(
    harness: _Harness, open_approval: OpenApproval
) -> None:
    approval = await open_approval(awaiting_ready=True)

    first = await ReviewCard(await _stored(approval)).dismiss("U_NOBODY")
    again = await ReviewCard(await _stored(approval)).dismiss("U_GRACE")

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
    await ReviewCard(await _stored(approval)).dismiss("U_GRACE")

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
    await ReviewCard(await _stored(approval)).dismiss("U_GRACE")

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
    monkeypatch.setattr(ReviewCard, "render", AsyncMock(return_value=("card", [])))
    monkeypatch.setattr(ReviewCard, "refresh", AsyncMock())

    sent = await voting.request_broadcast(approval)
    assert sent.message == "Sent to <#C_OTHER>."
    stored = await _stored(approval)
    assert stored.slack_channel_id == "C1"
    assert stored.slack_copy_channel_id == "C_OTHER"
    assert stored.slack_copy_ts == "9.0"
    assert stored.slack_broadcast is False


async def test_author_dm_success_is_quiet_only_when_the_status_card_updates(
    open_approval: OpenApproval, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import asynccontextmanager

    from openswe.human_review import clicks

    approval = await open_approval(awaiting_ready=True)
    approval.slack_dm_channel_id = "D_ADA"
    approval.slack_dm_message_ts = "4.0"
    approval = await approval.save()

    @asynccontextmanager
    async def lock(*args: object, **kwargs: object):
        yield

    monkeypatch.setattr(clicks, "slack_thread_mutation_lock", lock)
    monkeypatch.setattr(clicks, "langgraph_client", lambda: None)
    ephemeral = AsyncMock(return_value=True)
    monkeypatch.setattr(clicks, "post_slack_ephemeral_message", ephemeral)
    updated = AsyncMock(return_value=True)
    monkeypatch.setattr(ReviewCard, "refresh_author_dm", updated)
    handle = AsyncMock(return_value=Outcome("Dismissed.", dm_card_success=True))

    async def click(channel: str) -> None:
        await clicks.answer_click(
            str(approval.id),
            channel_id=channel,
            thread_ts="4.0",
            slack_user_id="U_ADA",
            handle=handle,
        )

    await click("D_ADA")
    ephemeral.assert_not_awaited()
    await click("C1")
    assert ephemeral.call_args.args[2] == "Dismissed."
    updated.return_value = False
    await click("D_ADA")
    assert ephemeral.await_count == 2
    handle.return_value = Outcome("GitHub did not mark the pull request ready: nope")
    updated.return_value = True
    await click("D_ADA")
    assert "nope" in ephemeral.call_args.args[2]


async def test_failed_pending_restore_preserves_rejected_vote(
    harness: _Harness, open_approval: OpenApproval, monkeypatch: pytest.MonkeyPatch
) -> None:
    approval = await open_approval(awaiting_ready=True)
    answers: list[Outcome] = []

    async def answer(
        request_id: str,
        *,
        handle: Callable[[HumanReviewRequest], Awaitable[Outcome]],
        **kwargs: object,
    ) -> Outcome:
        outcome = await handle(approval)
        answers.append(outcome)
        return outcome

    monkeypatch.setattr(voting, "answer_click", answer)
    monkeypatch.setattr(
        voting,
        "update_slack_message",
        AsyncMock(side_effect=[True, SlackRequestError("ratelimited")]),
    )
    await voting.process_vote(
        str(approval.id),
        decision="ready",
        person={"id": "slack:U_GRACE"},
        channel_id="C1",
        thread_ts="1.0",
        message_ts="2.0",
        message_blocks=[{"type": "actions"}],
    )
    assert answers[0].message == "Only the pull request's author can mark it ready for review."
