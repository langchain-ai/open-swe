import importlib
from typing import Any

import pytest

slack_read_tool = importlib.import_module("agent.tools.slack_read_thread_messages")
slack_utils = importlib.import_module("agent.utils.slack")


async def test_read_thread_returns_pre_fetch_version(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    async def fetch(channel_id: str, thread_ts: str) -> list[dict[str, Any]]:
        events.append("fetch")
        return [{"ts": thread_ts, "text": "hello", "user": "U1"}]

    async def names(user_ids: list[str]) -> dict[str, str]:
        return {"U1": "Alice"}

    async def version(_client: Any, channel_id: str, thread_ts: str) -> int:
        assert (channel_id, thread_ts) == ("C1", "1.0")
        events.append("version")
        return 3

    monkeypatch.setattr(slack_utils, "fetch_slack_thread_messages", fetch)
    monkeypatch.setattr(slack_utils, "get_slack_user_names", names)
    monkeypatch.setattr(slack_read_tool, "get_slack_thread_version", version)
    monkeypatch.setattr(slack_read_tool, "langgraph_client", object)

    result = await slack_read_tool.slack_read_thread_messages("C1", "1.0")

    assert result["success"] is True
    assert result["thread_version"] == 3
    assert events == ["version", "fetch"]
    assert result["formatted"] == "@Alice(U1) [message_ts=1.0]: hello"
