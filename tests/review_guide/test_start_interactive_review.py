import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from openswe.mcp.caller import ToolCaller
from openswe.review_guide import launch

tool = importlib.import_module("openswe.tools.start_interactive_review")

PR_URL = "https://github.com/langchain-ai/open-swe/pull/3557"


@pytest.mark.parametrize(
    ("source", "channel_id", "visibility", "is_private"),
    [
        ("slack", "D123", "private", True),
        ("slack", "D123", "public", True),
        ("slack", "C123", "public", False),
        ("dashboard", "C123", "private", True),
        ("dashboard", "C123", "public", False),
    ],
)
async def test_review_runs_where_it_was_asked_and_keeps_its_privacy(
    monkeypatch: pytest.MonkeyPatch, source: str, channel_id: str, visibility: str, is_private: bool
) -> None:
    requester = SimpleNamespace(id=uuid4())
    for_identity = AsyncMock(return_value=requester)
    for_login = AsyncMock(return_value=requester)
    monkeypatch.setattr(launch.User, "for_identity", for_identity)
    monkeypatch.setattr(launch.User, "for_login", for_login)
    cfg = SimpleNamespace(
        thread_id="source",
        source=source,
        github_login="alice",
        slack_thread=SimpleNamespace(triggering_user_id="U123", team_id="T123"),
        workspace_slug="default",
        mcp_caller=None,
    )
    monkeypatch.setattr(tool.RunConfig, "from_runtime", Mock(return_value=cfg))
    monkeypatch.setattr(
        launch.THREADS,
        "get",
        AsyncMock(
            return_value=SimpleNamespace(
                metadata={
                    "visibility": visibility,
                    "source_context": {
                        "slack_thread": {"channel_id": channel_id, "thread_ts": "1.0"}
                    },
                }
            )
        ),
    )
    start = AsyncMock(return_value=launch.StartedGuide(thread_id="source"))
    monkeypatch.setattr(tool, "start_review_guide", start)

    result = await tool.start_interactive_review(PR_URL)

    assert result == {"success": True, "where": "this conversation"}
    request = start.call_args.args[0]
    assert request.code_channel is False
    origin = request.origin
    assert origin.is_private is is_private
    assert origin.requester_id == requester.id
    assert origin.source_thread_id == "source"
    if source == "dashboard":
        for_login.assert_awaited_once_with("github", "alice")
        assert origin.slack_thread is None
    else:
        for_identity.assert_awaited_once_with("slack", "U123")
        assert origin.slack_thread is not None
        assert origin.slack_thread.location == (channel_id, "1.0")


@pytest.mark.parametrize("slack_user_id", ["U777", ""])
async def test_mcp_caller_opens_a_private_channel_only_for_their_own_linked_slack_account(
    monkeypatch: pytest.MonkeyPatch, slack_user_id: str
) -> None:
    caller = SimpleNamespace(id=uuid4(), slack_user_id=slack_user_id, slack_team_id="T9")
    for_login = AsyncMock(return_value=caller)
    monkeypatch.setattr(launch.User, "for_login", for_login)
    start = AsyncMock(return_value=launch.StartedGuide(thread_id="new", channel_id="C456"))
    monkeypatch.setattr(tool, "start_review_guide", start)

    result = await ToolCaller(login="alice", email=None).invoke(
        "start_interactive_review", {"pr_url": PR_URL, "invite": ["U888"]}
    )

    assert isinstance(result.content, str)
    content = json.loads(result.content)
    for_login.assert_awaited_with("github", "alice")
    if not slack_user_id:
        assert content["success"] is False
        assert "Slack account is not linked" in content["error"]
        start.assert_not_awaited()
        return
    assert content["channel_url"] == "https://slack.com/archives/C456"
    request = start.call_args.args[0]
    assert (request.owner, request.repo, request.number) == ("langchain-ai", "open-swe", 3557)
    assert request.code_channel is True
    assert request.invite == ["U888"]
    assert request.origin.requester_id == caller.id
    assert request.origin.is_private is True
    assert request.origin.source_thread_id is None
    assert request.origin.slack_thread is None
