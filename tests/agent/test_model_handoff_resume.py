from collections.abc import Sequence
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.pregel import Pregel

import agent.server as server
from agent.dashboard.workspace_settings import WorkspaceSettings
from agent.middleware.model_fallback import ModelFallbackMiddleware
from agent.middleware.model_selection import ModelSelectionMiddleware, ModelSelectionState
from agent.utils.thread_settings import ThreadSettings


@pytest.fixture
def factory_settings(monkeypatch: pytest.MonkeyPatch) -> ThreadSettings:
    settings: ThreadSettings = {
        "model_id": "openai:gpt-6-sol",
        "effort": "high",
        "model_routing_enabled": False,
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
    return settings


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
    factory_settings: ThreadSettings,
    source: Literal["dashboard", "slack"],
    routing: bool,
    requested_model: str | None,
) -> None:
    settings = factory_settings
    settings["model_routing_enabled"] = routing

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


@pytest.mark.parametrize(
    ("default_model", "requested_model"),
    [
        ("openai:gpt-6-sol", "anthropic:claude-opus-5-5"),
        ("anthropic:claude-opus-5-5", "openai:gpt-6-sol"),
    ],
)
async def test_handoff_survives_requested_provider_outage(
    monkeypatch: pytest.MonkeyPatch,
    factory_settings: ThreadSettings,
    default_model: str,
    requested_model: str,
) -> None:
    factory_settings["model_id"] = default_model
    monkeypatch.delenv("LLM_FALLBACK_MODEL_ID", raising=False)
    attempts: list[str] = []

    class ProviderModel(FakeListChatModel):
        async def _agenerate(
            self,
            messages: list[BaseMessage],
            stop: list[str] | None = None,
            run_manager: AsyncCallbackManagerForLLMRun | None = None,
            **kwargs: object,
        ) -> ChatResult:
            model_id = self.responses[0]
            attempts.append(model_id)
            if model_id.split(":")[0] == requested_model.split(":")[0]:
                raise TimeoutError("Provider unavailable")
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=model_id))])

    monkeypatch.setattr(
        server, "_make_model_or_defer", lambda model_id, **_: ProviderModel(responses=[model_id])
    )

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr("agent.middleware.model_fallback.asyncio.sleep", no_delay)

    def assemble(*, model: ProviderModel, middleware: Sequence[object], **_: object) -> Pregel:
        selected: list[AgentMiddleware[ModelSelectionState, None]] = [
            item
            for item in middleware
            if isinstance(item, (ModelSelectionMiddleware, ModelFallbackMiddleware))
        ]
        return create_agent(model, middleware=selected)

    monkeypatch.setattr(server, "create_deep_agent", assemble)
    config: RunnableConfig = {
        "configurable": {
            "thread_id": "handoff-outage",
            "__is_for_execution__": True,
            "source": "slack",
        }
    }
    graph = await server.build_agent(config)
    result = await graph.ainvoke(
        {"messages": [HumanMessage("Continue")], "requested_model": requested_model}, config
    )
    assert result["messages"][-1].content == default_model
    assert attempts == [requested_model, default_model]
