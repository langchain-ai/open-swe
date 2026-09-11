import importlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

slack_accept_tool = importlib.import_module("agent.slack.tools.accept_untagged_message")


def _config(*, untagged_reply: bool = True) -> dict[str, Any]:
    return {
        "run_id": "run-1",
        "configurable": {
            "thread_id": "thread-1",
            "run_id": "run-1",
            "untagged_reply": untagged_reply,
            "slack_thread": {"channel_id": "C1", "thread_ts": "1.0"},
        },
    }


async def test_accept_untagged_message_starts_thinking_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = object()
    status = AsyncMock(return_value=True)
    maintain = AsyncMock()
    monkeypatch.setattr(slack_accept_tool, "get_config", _config)
    monkeypatch.setattr(slack_accept_tool, "langgraph_client", lambda: client)
    monkeypatch.setattr(
        slack_accept_tool,
        "get_active_slack_thread",
        AsyncMock(return_value={"channel_id": "C1", "thread_ts": "1.0"}),
    )
    monkeypatch.setattr(slack_accept_tool, "set_slack_thread_status", status)
    monkeypatch.setattr(slack_accept_tool, "maintain_slack_thinking_status", maintain)

    result = await slack_accept_tool.slack_accept_untagged_message()
    await next(iter(slack_accept_tool._status_tasks))

    assert result == {"success": True}
    status.assert_awaited_once_with("C1", "1.0", "Thinking...")
    maintain.assert_awaited_once_with(
        client=client,
        thread_id="thread-1",
        run_id="run-1",
        channel_id="C1",
        thread_ts="1.0",
    )


async def test_accept_untagged_message_rejects_explicit_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_accept_tool, "get_config", lambda: _config(untagged_reply=False))
    status = AsyncMock()
    monkeypatch.setattr(slack_accept_tool, "set_slack_thread_status", status)

    result = await slack_accept_tool.slack_accept_untagged_message()

    assert result == {"success": False, "error": "This run was explicitly requested"}
    status.assert_not_awaited()
