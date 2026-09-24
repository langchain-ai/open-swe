import importlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

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


@pytest.mark.parametrize(
    ("requested_model", "fable_enabled", "selection"),
    [
        ("sonnnet", True, None),
        ("anthropic:claude-fable-5-1", False, None),
        ("anthropic:claude-haiku-4-5", True, "explicit"),
    ],
)
async def test_invalid_or_overridden_model_does_not_commit(
    monkeypatch, requested_model, fable_enabled, selection
) -> None:
    commit = AsyncMock()
    monkeypatch.setattr(exit_pre_routed_mode_module, "commit_route", commit)
    monkeypatch.setattr(
        exit_pre_routed_mode_module, "get_team_fable_enabled", AsyncMock(return_value=fable_enabled)
    )
    monkeypatch.setattr(
        "agent.run_config.get_config",
        lambda: {"configurable": {"thread_id": "t1", "model_selection": selection}},
    )

    result = await exit_pre_routed_mode_module.exit_pre_routed_mode(
        "fast", "Answer a question", "call-1", requested_model=requested_model
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    commit.assert_not_awaited()


@pytest.mark.parametrize(
    ("requested_model", "effort"),
    [("anthropic:claude-haiku-4-5", "none"), ("anthropic:claude-fable-5-1", "high")],
)
async def test_requested_model_handoff_persists_and_routes_first_turn_and_followup(
    monkeypatch, requested_model, effort
) -> None:
    from agent import model_routing
    from agent.middleware.model_selection import ModelSelectionMiddleware
    from agent.utils import thread_settings, ttl_cache

    stored = {
        "model_id": "openai:gpt-5.6-sol",
        "effort": "xhigh",
        "model_routing_enabled": True,
        "repo_instructions": "Keep this setting",
    }
    client = MagicMock()
    client.threads.get = AsyncMock(return_value={"metadata": {"agent_settings": stored}})
    client.threads.update = AsyncMock()
    title = AsyncMock()
    monkeypatch.setattr(model_routing, "get_client", lambda: client)
    monkeypatch.setattr(model_routing, "name_thread", title)
    monkeypatch.setattr(ttl_cache, "set_cached", lambda *args: None)

    async def uncached(key, ttl, loader):
        return await loader()

    monkeypatch.setattr(ttl_cache, "cached", uncached)
    monkeypatch.setattr(
        exit_pre_routed_mode_module, "get_team_fable_enabled", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "agent.run_config.get_config", lambda: {"configurable": {"thread_id": "requested-model"}}
    )
    models = {route: MagicMock() for route in ("fast", "balanced", "performance")}
    requested = MagicMock()
    middleware = ModelSelectionMiddleware(models, requested_model_factory=lambda _: requested)
    state = await middleware.abefore_agent({"messages": []}, MagicMock())
    assert middleware._model_for(state) is models["fast"]

    result = await as_tool(exit_pre_routed_mode_module.exit_pre_routed_mode).ainvoke(
        {
            "name": "exit_pre_routed_mode",
            "args": {
                "model_route": "performance",
                "title": "Answer a question",
                "requested_model": requested_model,
            },
            "id": "call-1",
            "type": "tool_call",
        }
    )
    assert isinstance(result, Command)
    state.update(result.update)
    assert state["pre_routed"] is False
    assert middleware._model_for(state) is requested
    assert requested_model in state["messages"][0].content
    title.assert_awaited_once()
    saved = client.threads.update.call_args.kwargs["metadata"]["agent_settings"]
    assert saved == {
        **stored,
        "model_route": "performance",
        "model_id": requested_model,
        "requested_model": requested_model,
        "effort": effort,
    }
    normalized, changed = thread_settings.normalize_thread_settings(saved)
    assert not changed
    followup = ModelSelectionMiddleware(
        models,
        initial_route=normalized["model_route"],
        initial_requested_model=normalized["requested_model"],
        requested_model_factory=lambda _: requested,
    )
    followup_state = await followup.abefore_agent({"messages": []}, MagicMock())
    assert followup_state["pre_routed"] is False
    assert followup._model_for(followup_state) is requested
    assert (
        followup._model_for({**followup_state, "messages": [], "plan_mode": True})
        is models["performance"]
    )
