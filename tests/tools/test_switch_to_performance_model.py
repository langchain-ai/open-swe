from unittest.mock import AsyncMock

import pytest

from agent.tools import switch_to_performance_model as switch_tool
from agent.utils.thread_settings import ThreadSettings


async def test_switch_persists_and_updates_the_running_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    module = importlib.import_module("agent.tools.switch_to_performance_model")
    settings: ThreadSettings = {
        "model_id": "openai:gpt-5.4",
        "routing_models": {"performance": {"model_id": "openai:gpt-6-astra", "effort": "low"}},
    }
    monkeypatch.setattr(module, "get_config", lambda: {"configurable": {"thread_id": "current"}})
    monkeypatch.setattr(module, "langgraph_client", lambda: object())
    monkeypatch.setattr(module, "load_thread_settings", AsyncMock(return_value=settings))
    saved = AsyncMock()
    monkeypatch.setattr(module, "store_thread_settings", saved)
    result = await switch_tool(tool_call_id="switch-call")
    stored = saved.call_args.args[2]
    assert stored["model_id"] == "openai:gpt-6-astra"
    assert stored["model_routing_enabled"] is False
    assert result.update["requested_model"] == stored["model_id"]
    assert result.update["requested_effort"] == stored["effort"]
    assert result.update["messages"][0].tool_call_id == "switch-call"
