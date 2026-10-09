from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openswe.slack.tools import start_review_channel as tool


@pytest.mark.parametrize(
    ("channel_id", "visibility", "is_private"),
    [("D123", "private", True), ("D123", "public", True), ("C123", "public", False)],
)
async def test_review_channel_accepts_dms_and_preserves_privacy(
    monkeypatch: pytest.MonkeyPatch, channel_id: str, visibility: str, is_private: bool
) -> None:
    location = SimpleNamespace(triggering_user_id="U123", team_id="T123", dump=lambda: {})
    cfg = SimpleNamespace(thread_id="source", slack_thread=location, workspace_slug="default")
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

    result = await tool.slack_start_review_channel(
        "https://github.com/langchain-ai/open-swe/pull/3557"
    )

    assert result["success"] is True
    request = start.call_args.args[0]
    assert request.is_private is is_private
    assert request.requester_slack_id == "U123"
    assert request.origin_channel_id == channel_id
