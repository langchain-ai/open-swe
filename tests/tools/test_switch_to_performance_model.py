from unittest.mock import AsyncMock

import pytest

from openswe.tools import switch_to_performance_model as switch_tool
from openswe.utils.thread_settings import ThreadSettings


@pytest.mark.parametrize("reply_thread_ts", [None, "", "reply-thread"])
async def test_switch_persists_and_updates_the_running_model(
    monkeypatch: pytest.MonkeyPatch,
    reply_thread_ts: str | None,
) -> None:
    import importlib

    module = importlib.import_module("openswe.tools.switch_to_performance_model")
    settings: ThreadSettings = {
        "model_id": "openai:gpt-5.4",
        "routing_models": {"performance": {"model_id": "openai:gpt-6-astra", "effort": "low"}},
    }
    monkeypatch.setattr(
        module,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "current",
                "run_id": "current-run",
                "slack_thread": (
                    {
                        "channel_id": "C123",
                        "thread_ts": "original-thread",
                        "reply_thread_ts": reply_thread_ts,
                    }
                    if reply_thread_ts is not None
                    else None
                ),
            }
        },
    )
    monkeypatch.setattr(module, "langgraph_client", lambda: object())
    monkeypatch.setattr(module, "load_thread_settings", AsyncMock(return_value=settings))
    feedback = AsyncMock(return_value=True)
    monkeypatch.setattr(module, "create_langsmith_feedback", feedback)
    notice = AsyncMock(return_value="notice-ts")
    monkeypatch.setattr(module, "post_slack_thread_reply_with_ts", notice)
    saved = AsyncMock()
    monkeypatch.setattr(module, "store_thread_settings", saved)
    result = await switch_tool(tool_call_id="switch-call")
    assert feedback.call_args.args == ("current-run", "performance_model_switch_tool")
    stored = saved.call_args.args[2]
    assert stored["model_id"] == "openai:gpt-6-astra"
    assert stored["model_routing_enabled"] is False
    assert result.update["requested_model"] == stored["model_id"]
    assert result.update["requested_effort"] == stored["effort"]
    assert result.update["messages"][0].tool_call_id == "switch-call"
    if reply_thread_ts is None:
        notice.assert_not_awaited()
    else:
        confirmation = result.update["messages"][0].content
        notice.assert_awaited_once_with(
            "C123",
            reply_thread_ts or "original-thread",
            confirmation,
            blocks=[{"type": "context", "elements": [{"type": "mrkdwn", "text": confirmation}]}],
        )
