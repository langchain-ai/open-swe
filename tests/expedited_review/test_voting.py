"""PostgreSQL regressions for clicks on an expedited review card."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from agent.expedited_review import lifecycle, voting
from agent.expedited_review.approvals import ExpeditedApproval
from agent.users import User
from tests.expedited_review.conftest import OpenApproval


class _Harness:
    def __init__(self) -> None:
        self.agent_prompts: list[str] = []
        self.marked_ready: list[str] = []

    async def notify_agent(self, approval: ExpeditedApproval, prompt: str) -> None:
        self.agent_prompts.append(prompt)

    async def mark_ready(self, owner: str, repo: str, number: int, action: object, token: str):
        self.marked_ready.append(token)


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
    monkeypatch.setattr(lifecycle, "refresh_card", AsyncMock())
    monkeypatch.setattr(lifecycle, "notify_agent", h.notify_agent)
    return h


async def _click(
    approval: ExpeditedApproval, slack_user: str, decision: voting.CardAction = "approve"
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


async def test_one_non_author_approval_approves_and_wakes_the_agent_once(
    harness: _Harness, open_approval: OpenApproval
) -> None:
    approval = await open_approval()

    await _click(approval, "U_GRACE")
    await _click(approval, "U_LINUS")

    stored = await _stored(approval)
    assert stored.state == "open"
    assert stored.approved
    assert sorted(stored.approvers) == ["grace", "linus"]
    assert all(vote.github_review_id is None for vote in stored.votes)
    assert len(harness.agent_prompts) == 1
    assert "@grace" in harness.agent_prompts[0]


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


async def test_rejection_ends_the_vote_and_tells_the_agent_once(
    harness: _Harness, open_approval: OpenApproval
) -> None:
    approval = await open_approval()

    await _click(approval, "U_LINUS", decision="reject")
    late = await _click(approval, "U_GRACE")

    stored = await _stored(approval)
    assert stored.state == "rejected"
    assert stored.rejection is not None and stored.rejection.github_login == "linus"
    assert "no longer accepting" in late.message
    assert len(harness.agent_prompts) == 1
    assert "linus" in harness.agent_prompts[0]


async def test_only_one_open_approval_per_pull_request(open_approval: OpenApproval) -> None:
    approval = await open_approval()

    duplicate = ExpeditedApproval(pull_request_id=approval.pull_request_id, head_sha="zzz")
    with pytest.raises(IntegrityError):
        await duplicate.save()
    assert await ExpeditedApproval.active_for("lc", "repo", 7) is not None
