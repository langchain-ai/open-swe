import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openswe.mcp.caller import ToolCaller
from openswe.slack.tools import start_review_channel as tool

PR_URL = "https://github.com/langchain-ai/open-swe/pull/3557"


@pytest.mark.parametrize(
    ("channel_id", "visibility", "is_private"),
    [("D123", "private", True), ("D123", "public", True), ("C123", "public", False)],
)
async def test_review_channel_accepts_dms_and_preserves_privacy(
    monkeypatch: pytest.MonkeyPatch, channel_id: str, visibility: str, is_private: bool
) -> None:
    location = SimpleNamespace(triggering_user_id="U123", team_id="T123", dump=lambda: {})
    cfg = SimpleNamespace(
        thread_id="source",
        slack_thread=location,
        workspace_slug="default",
        mcp_caller=None,
    )
    monkeypatch.setattr(tool.RunConfig, "from_runtime", Mock(return_value=cfg))
    client = SimpleNamespace(
        threads=SimpleNamespace(
            get=AsyncMock(return_value={"metadata": {"visibility": visibility}})
        )
    )
    monkeypatch.setattr(tool, "langgraph_client", lambda: client)
    monkeypatch.setattr(
        tool,
        "get_active_slack_thread",
        AsyncMock(return_value={"channel_id": channel_id, "thread_ts": "1.0"}),
    )
    start = AsyncMock(return_value=SimpleNamespace(channel_id="C456"))
    monkeypatch.setattr(tool, "start_review_guide", start)

    result = await tool.slack_start_review_channel(PR_URL)

    assert result["success"] is True
    origin = start.call_args.args[0].origin
    assert origin.is_private is is_private
    assert origin.requester_slack_id == "U123"
    assert origin.channel_id == channel_id
    assert origin.source_thread_id == "source"


@pytest.mark.parametrize("slack_user_id", ["U777", ""])
async def test_mcp_caller_opens_a_private_channel_only_for_their_own_linked_slack_account(
    monkeypatch: pytest.MonkeyPatch, slack_user_id: str
) -> None:
    caller = SimpleNamespace(slack_user_id=slack_user_id, slack_team_id="T9")
    for_login = AsyncMock(return_value=caller)
    monkeypatch.setattr(tool.User, "for_login", for_login)
    start = AsyncMock(return_value=SimpleNamespace(channel_id="C456"))
    monkeypatch.setattr(tool, "start_review_guide", start)

    result = await ToolCaller(login="alice", email=None).invoke(
        "slack_start_review_channel", {"pr_url": PR_URL, "invite": ["U888"]}
    )

    assert isinstance(result.content, str)
    content = json.loads(result.content)
    for_login.assert_awaited_once_with("github", "alice")
    if not slack_user_id:
        assert content["success"] is False
        assert "Slack account is not linked" in content["error"]
        start.assert_not_awaited()
        return
    assert content["channel_url"] == "https://slack.com/archives/C456"
    request = start.call_args.args[0]
    assert (request.owner, request.repo, request.number) == ("langchain-ai", "open-swe", 3557)
    assert request.invite == ["U888"]
    assert request.origin.requester_slack_id == "U777"
    assert request.origin.is_private is True
    assert request.origin.source_thread_id is None
    assert request.origin.channel_id == ""
