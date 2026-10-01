import importlib
from unittest.mock import AsyncMock

import pytest

request_human_review_tools = importlib.import_module("agent.tools.request_human_review")

_PR_URL = "https://github.com/acme/widgets/pull/42"


@pytest.mark.parametrize("tool_name", ["assign_human_reviewer", "auto_assign_human_reviewer"])
async def test_assignment_rejects_open_expedited_card(
    monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    monkeypatch.setattr(
        request_human_review_tools.HumanReviewRequest,
        "active_for",
        AsyncMock(return_value=type("Request", (), {"kind": "expedited"})()),
    )
    tool = getattr(request_human_review_tools, tool_name)
    arguments = {"pr_url": _PR_URL}
    if tool_name == "assign_human_reviewer":
        arguments["github_login"] = "octocat"

    result = await tool(**arguments)

    assert result["success"] is False
    assert "open expedited approval card" in result["error"]
    assert "no open review request" not in result["error"]


@pytest.mark.parametrize("tool_name", ["assign_human_reviewer", "auto_assign_human_reviewer"])
async def test_assignment_reports_missing_request(
    monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    monkeypatch.setattr(
        request_human_review_tools.HumanReviewRequest,
        "active_for",
        AsyncMock(return_value=None),
    )
    tool = getattr(request_human_review_tools, tool_name)
    arguments = {"pr_url": _PR_URL}
    if tool_name == "assign_human_reviewer":
        arguments["github_login"] = "octocat"

    result = await tool(**arguments)

    assert result == {
        "success": False,
        "error": "This pull request has no open review request to assign a reviewer to.",
    }
