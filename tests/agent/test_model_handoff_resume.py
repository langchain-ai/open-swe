from collections.abc import Sequence
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.pregel import Pregel

import agent.server as server
from agent.dashboard.workspace_settings import WorkspaceSettings
from agent.middleware.model_selection import ModelSelectionMiddleware
from agent.utils.thread_settings import ThreadSettings


@pytest.mark.parametrize(
    ("source", "routing", "requested_model"),
    [
        ("dashboard", True, "anthropic:claude-opus-5-5"),
        ("slack", False, "anthropic:claude-opus-5-5"),
        ("slack", False, None),
    ],
)
async def test_resume_after_model_handoff(
    monkeypatch: pytest.MonkeyPatch,
    source: Literal["dashboard", "slack"],
    routing: bool,
    requested_model: str | None,
) -> None:
    settings: ThreadSettings = {
        "model_id": "openai:gpt-6-sol",
        "effort": "high",
        "model_routing_enabled": routing,
        "model_handoff_complete": False,
    }
    for name in (
        "resolve_github_login",
        "private_credential_login",
        "_cached_profile",
        "store_thread_settings",
    ):
        monkeypatch.setattr(server, name, AsyncMock(return_value=None))
    for name in ("_admin_thread", "_private_thread", "_bridged_thread"):
        monkeypatch.setattr(server, name, AsyncMock(return_value=False))
    for name in ("_mcp_tools_for", "_notion_tools_for"):
        monkeypatch.setattr(server, name, AsyncMock(return_value=[]))
    monkeypatch.setattr(
        server, "load_thread_settings", AsyncMock(side_effect=lambda *_: settings.copy())
    )
    monkeypatch.setattr(
        server, "cached_workspace_settings", AsyncMock(return_value=WorkspaceSettings({}))
    )
    monkeypatch.setattr(server, "get_cached_sandbox_backend", MagicMock())
    monkeypatch.setattr(server, "tools_base_url", lambda: None)
    monkeypatch.setattr(
        server,
        "_make_model_or_defer",
        lambda model_id, **_: FakeListChatModel(responses=[model_id]),
    )

    checkpointer = InMemorySaver()

    def assemble(*, model: FakeListChatModel, middleware: Sequence[object], **_: object) -> Pregel:
        # Exercise the factory's routing middleware with real checkpoint execution,
        # without invoking unrelated sandbox, tool, and analytics middleware.
        selection = [item for item in middleware if isinstance(item, ModelSelectionMiddleware)]
        return create_agent(
            model,
            middleware=selection,
            checkpointer=checkpointer,
            interrupt_before=["ModelSelectionMiddleware.before_model"] if selection else [],
        )

    monkeypatch.setattr(server, "create_deep_agent", assemble)
    config: RunnableConfig = {
        "configurable": {
            "thread_id": "handoff-resume",
            "__is_for_execution__": True,
            "source": source,
        }
    }
    initial = await server.build_agent(config)
    await initial.ainvoke(
        {
            "messages": [HumanMessage("Continue the task")],
            "requested_model": requested_model,
            "model_route": "default",
        },
        config,
    )
    assert (await initial.aget_state(config)).next == ("ModelSelectionMiddleware.before_model",)

    settings["model_handoff_complete"] = True
    settings["requested_model"] = requested_model
    settings["model_routing_enabled"] = False
    if requested_model:
        settings["model_id"] = requested_model

    resumed = await server.build_agent(config)
    result = await resumed.ainvoke(None, config)
    assert isinstance(result["messages"][-1], AIMessage)
    assert result["messages"][-1].content == settings["model_id"]
    assert (await resumed.aget_state(config)).next == ()
