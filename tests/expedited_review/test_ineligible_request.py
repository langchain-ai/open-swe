from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.expedited_review.eligibility import ChangedFile
from agent.human_review import lifecycle
from agent.human_review.requests import HumanReviewRequest
from tests.expedited_review.conftest import OpenApproval

tool = import_module("agent.tools.expedite_pr_approval")


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
