import importlib
from typing import Any

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool as as_tool
from langgraph.types import Command

exit_pre_routed_mode_module = importlib.import_module("agent.tools.exit_pre_routed_mode")


async def test_exit_pre_routed_mode_commits_route_and_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    committed: dict[str, Any] = {}

    async def fake_commit_route(
        *, thread_id: str, cfg: Any, model_route: str, title: str
    ) -> dict[str, Any]:
        committed.update(thread_id=thread_id, model_route=model_route, title=title)
        return {"model_route": model_route, "pre_routed": False}

    monkeypatch.setattr(
        "agent.run_config.get_config", lambda: {"configurable": {"thread_id": "t1"}}
    )
    monkeypatch.setattr(exit_pre_routed_mode_module, "commit_route", fake_commit_route)

    wrapped = as_tool(exit_pre_routed_mode_module.exit_pre_routed_mode)
    result = await wrapped.ainvoke(
        {
            "name": "exit_pre_routed_mode",
            "args": {"model_route": "performance", "title": "Redesign model routing"},
            "id": "call-1",
            "type": "tool_call",
        }
    )

    assert committed == {
        "thread_id": "t1",
        "model_route": "performance",
        "title": "Redesign model routing",
    }
    assert isinstance(result, Command)
    assert result.update is not None
    assert result.update["model_route"] == "performance"
    assert result.update["pre_routed"] is False
    (message,) = result.update["messages"]
    assert isinstance(message, ToolMessage)
    assert message.tool_call_id == "call-1"
    assert "performance" in message.content
