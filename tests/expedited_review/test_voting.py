"""PostgreSQL regressions for recording expedited review votes."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from agent.expedited_review import lifecycle, voting
from agent.expedited_review.approvals import ExpeditedApproval
from agent.users import User
from tests.expedited_review.conftest import OpenApproval


class _Harness:
    def __init__(self) -> None:
        self.agent_prompts: list[str] = []

    async def notify_agent(self, approval: ExpeditedApproval, prompt: str) -> None:
        self.agent_prompts.append(prompt)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> _Harness:
    h = _Harness()
    monkeypatch.setattr(voting, "repo_token", AsyncMock(return_value="app-token"))
    monkeypatch.setattr(voting, "has_repo_write_permission", AsyncMock(return_value=True))
    monkeypatch.setattr(voting, "get_valid_access_token", AsyncMock(return_value="user-token"))
    monkeypatch.setattr(voting, "refresh_card", AsyncMock())
    monkeypatch.setattr(voting, "notify_agent", h.notify_agent)
    monkeypatch.setattr(lifecycle, "refresh_card", AsyncMock())
    monkeypatch.setattr(lifecycle, "notify_agent", h.notify_agent)
    return h


async def _vote(approval: ExpeditedApproval, slack_user: str, decision: str = "approve"):
    current = await ExpeditedApproval.get(approval.id)
    assert current is not None
    return await voting.handle_vote(
        current,
        decision="approve" if decision == "approve" else "reject",
        user=await User.for_person({"id": f"slack:{slack_user}", "platform": "slack"}),
    )


async def test_approvals_are_only_recorded_and_the_second_wakes_the_agent_once(
    harness: _Harness, open_approval: OpenApproval
) -> None:
    approval = await open_approval()

    await _vote(approval, "U_GRACE")
    assert harness.agent_prompts == []
    await _vote(approval, "U_LINUS")
    await _vote(approval, "U_ADA")

    stored = await ExpeditedApproval.get(approval.id)
    assert stored is not None
    assert stored.state == "open"
    assert sorted(stored.approvers) == ["ada", "grace", "linus"]
    assert all(vote.github_review_id is None for vote in stored.votes)
    assert len(harness.agent_prompts) == 1
    assert "@grace" in harness.agent_prompts[0] and "@linus" in harness.agent_prompts[0]


async def test_one_person_cannot_approve_twice(
    harness: _Harness, open_approval: OpenApproval
) -> None:
    approval = await open_approval()

    await _vote(approval, "U_GRACE")
    outcome = await _vote(approval, "U_GRACE")

    stored = await ExpeditedApproval.get(approval.id)
    assert stored is not None
    assert "already" in outcome.message
    assert stored.approvers == ["grace"]


async def test_unlinked_read_only_or_tokenless_users_cannot_vote(
    harness: _Harness, open_approval: OpenApproval, monkeypatch: pytest.MonkeyPatch
) -> None:
    approval = await open_approval()

    unlinked = await _vote(approval, "U_NOBODY")
    monkeypatch.setattr(voting, "get_valid_access_token", AsyncMock(return_value=None))
    tokenless = await _vote(approval, "U_GRACE")
    author = await _vote(approval, "U_ADA")
    monkeypatch.setattr(voting, "has_repo_write_permission", AsyncMock(return_value=False))
    read_only = await _vote(approval, "U_LINUS")

    stored = await ExpeditedApproval.get(approval.id)
    assert stored is not None
    assert "not linked" in unlinked.message
    assert "no GitHub token" in tokenless.message
    assert "recorded" in author.message
    assert "write access" in read_only.message
    assert stored.approvers == ["ada"]


async def test_rejection_ends_the_vote_and_tells_the_agent_once(
    harness: _Harness, open_approval: OpenApproval
) -> None:
    approval = await open_approval()
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


async def test_only_one_open_approval_per_pull_request(open_approval: OpenApproval) -> None:
    approval = await open_approval()

    duplicate = ExpeditedApproval(pull_request_id=approval.pull_request_id, head_sha="zzz")
    with pytest.raises(IntegrityError):
        await duplicate.save()
    assert await ExpeditedApproval.active_for("lc", "repo", 7) is not None
