from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openswe.expedited_review.eligibility import ChangedFile
from openswe.expedited_review.readiness import PullRequestSnapshot, Readiness
from openswe.human_review import lifecycle, standard
from openswe.human_review.requests import HumanReviewRequest
from openswe.slack.client import GitHubPrRef
from tests.expedited_review.conftest import OpenApproval

tool = import_module("openswe.tools.expedite_pr_approval")


@pytest.mark.asyncio
@pytest.mark.parametrize("thread_id", ["thread-1", "another-thread"])
@pytest.mark.parametrize(
    "reason", ["dismissed", "cancelled by the agent", "the pull request was closed"]
)
async def test_dismissal_prevents_recreating_expedited_request(
    monkeypatch: pytest.MonkeyPatch, open_approval: OpenApproval, thread_id: str, reason: str
) -> None:
    approval = await open_approval(awaiting_ready=True)
    monkeypatch.setattr(lifecycle, "refresh_card_in_thread", AsyncMock())
    if reason == "dismissed":
        await lifecycle.dismiss_request(approval, "U_ADA")
    else:
        await lifecycle.retire(approval, "cancelled", reason)
    monkeypatch.setattr(tool, "get_config", lambda: {})
    monkeypatch.setattr(
        tool.RunConfig, "from_config", lambda _: SimpleNamespace(thread_id=thread_id)
    )
    monkeypatch.setattr(
        tool,
        "get_workspace_settings",
        AsyncMock(return_value=SimpleNamespace(expedited_review_enabled=True)),
    )
    monkeypatch.setattr(tool, "run_slack_location", AsyncMock(return_value=(None, None)))
    result = await tool.expedite_pr_approval("https://github.com/lc/repo/pull/7")
    assert result["success"] is False
    if reason == "dismissed":
        assert "dismissed" in result["error"]
    else:
        assert "no Slack location" in result["error"]
    assert await HumanReviewRequest.active_for("lc", "repo", 7) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("thread_id", ["thread-1", "another-thread"])
async def test_ineligible_update_retires_existing_card(
    monkeypatch: pytest.MonkeyPatch, open_approval: OpenApproval, thread_id: str
) -> None:
    approval = await open_approval()
    monkeypatch.setattr(tool, "get_config", lambda: {})
    monkeypatch.setattr(
        tool.RunConfig, "from_config", lambda _: SimpleNamespace(thread_id=thread_id)
    )
    monkeypatch.setattr(
        tool,
        "get_workspace_settings",
        AsyncMock(return_value=SimpleNamespace(expedited_review_enabled=True)),
    )
    monkeypatch.setattr(tool, "run_slack_location", AsyncMock(return_value=("C1", "1.0")))
    monkeypatch.setattr(tool, "resolve_github_token", AsyncMock(return_value=("token", None)))
    monkeypatch.setattr(
        tool, "fetch_pr", AsyncMock(return_value={"state": "open", "head": {"sha": "new"}})
    )
    monkeypatch.setattr(
        tool,
        "fetch_changed_files",
        AsyncMock(return_value=[ChangedFile(filename="src/app.py", additions=30, patch="+fixed")]),
    )
    monkeypatch.setattr(lifecycle, "refresh_card_in_thread", AsyncMock())
    result = await tool.expedite_pr_approval("https://github.com/lc/repo/pull/7")
    assert result["success"] is False
    assert "Not eligible" in result["error"]
    active = await HumanReviewRequest.active_for("lc", "repo", 7)
    assert (active is None) == (thread_id == "thread-1")
    retired = await HumanReviewRequest.get(approval.id)
    assert retired is not None
    assert retired.state == ("superseded" if thread_id == "thread-1" else "open")


@pytest.mark.asyncio
async def test_standard_review_replaces_author_only_request(
    monkeypatch: pytest.MonkeyPatch, open_approval: OpenApproval
) -> None:
    approval = await open_approval(awaiting_ready=True)
    monkeypatch.setattr(standard, "repo_token", AsyncMock(return_value="token"))
    snapshot = PullRequestSnapshot(
        state="open",
        merged=False,
        draft=False,
        head_sha="new",
        title="Fix",
        author="ada",
        mergeable=True,
        mergeable_state="clean",
        check_state="success",
        unresolved_threads=0,
    )
    monkeypatch.setattr(
        standard, "assess_readiness", AsyncMock(return_value=Readiness(snapshot, []))
    )
    monkeypatch.setattr(
        standard,
        "_target_channel",
        AsyncMock(return_value=SimpleNamespace(id="C1", name="reviews")),
    )
    monkeypatch.setattr(
        standard,
        "record_pull_request",
        AsyncMock(
            return_value=(
                approval.pull_request,
                SimpleNamespace(head_sha="new", body="Fix", author="ada"),
            )
        ),
    )
    monkeypatch.setattr(standard, "_schedule", AsyncMock(return_value=True))
    monkeypatch.setattr(standard, "post_standard_card", AsyncMock(return_value="3.0"))
    monkeypatch.setattr(
        standard, "_permalink", AsyncMock(return_value="https://slack.example/card")
    )
    monkeypatch.setattr(lifecycle, "refresh_card_in_thread", AsyncMock())
    result = await standard.request_review(
        GitHubPrRef("lc", "repo", 7, "https://github.com/lc/repo/pull/7"), standard.Origin()
    )
    assert result.success
    active = await HumanReviewRequest.active_for("lc", "repo", 7)
    assert active is not None and active.kind == "standard"
    retired = await HumanReviewRequest.get(approval.id)
    assert retired is not None and retired.state == "superseded"
