"""A loaded integration tool reaches the model a call is finally sent to."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, patch

import pytest
from deepagents import create_deep_agent
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from agent.dashboard.workspace_settings import WorkspaceSettings
from agent.utils.model import make_model
from tests.agent.test_agent_assembly_context import (
    _MODEL_DEFAULTS,
    _base_config,
    _capture_create_deep_agent_kwargs,
)
from tests.agent.test_agent_assembly_context import saved_thread_scope as saved_thread_scope

_OPUS = "anthropic:claude-opus-5-5"
_GPT = "openai:gpt-6-sol"


async def _search(query: str) -> str:
    """Search the workspace's Notion pages."""
    return query


_NOTION_SEARCH = StructuredTool.from_function(coroutine=_search, name="notion-search")


@dataclass
class _Sent:
    model_id: str
    payload: Mapping[str, object]


@dataclass
class _Providers:
    """Answers every provider call offline and records the request it would have sent."""

    failing: type[BaseChatModel] | None = None
    sent: list[_Sent] = field(default_factory=list)

    async def answer(
        self,
        model: ChatAnthropic | ChatOpenAI,
        messages: list[BaseMessage],
        stop: list[str] | None,
        **kwargs: object,
    ) -> ChatResult:
        model_id = (
            f"anthropic:{model.model}"
            if isinstance(model, ChatAnthropic)
            else f"openai:{model.model_name}"
        )
        payload = model._get_request_payload(messages, stop=stop, **kwargs)
        self.sent.append(_Sent(model_id, payload))
        loaded = any(isinstance(message, ToolMessage) for message in messages)
        if loaded and self.failing is not None and isinstance(model, self.failing):
            raise TimeoutError("provider unavailable")
        reply = (
            AIMessage("Done.")
            if loaded
            else AIMessage(
                "",
                tool_calls=[
                    {
                        "id": "load-call",
                        "name": "load_integration_tools",
                        "args": {"tool_names": ["notion-search"]},
                    }
                ],
            )
        )
        return ChatResult(generations=[ChatGeneration(message=reply)])


async def _run(
    providers: _Providers,
    *,
    thread_settings: dict[str, object],
    workspace_settings: WorkspaceSettings | None = None,
    model_route: str | None = None,
) -> None:
    config = _base_config()
    with patch("agent.server._notion_tools_for", AsyncMock(return_value=[_NOTION_SEARCH])):
        kwargs = await _capture_create_deep_agent_kwargs(
            config,
            thread_settings={"owner_login": "octocat", **thread_settings},
            workspace_settings=workspace_settings,
            make_model=make_model,
        )
    kwargs.pop("make_model_calls")
    kwargs["checkpointer"] = InMemorySaver()
    kwargs["store"] = InMemoryStore()
    agent = create_deep_agent(**kwargs)
    prepared = {
        "work_dir": "/workspace",
        "rendered_system_prompt": "You are Open SWE.",
        **({"model_route": model_route} if model_route else {}),
    }

    async def generate(
        model: ChatAnthropic | ChatOpenAI,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object = None,
        **kwargs: object,
    ) -> ChatResult:
        del run_manager
        return await providers.answer(model, messages, stop, **kwargs)

    with (
        patch("agent.server.PrepareAgentRunMiddleware._prepare", AsyncMock(return_value=prepared)),
        patch.object(ChatAnthropic, "_agenerate", generate),
        patch.object(ChatOpenAI, "_agenerate", generate),
    ):
        await agent.ainvoke({"messages": [HumanMessage("Find the plan in Notion.")]}, config)


def _openai_offer(payload: Mapping[str, object]) -> tuple[list[str], list[str]]:
    """The tool names an OpenAI request sends in ``tools``, and those it adds mid-conversation."""
    tools = payload.get("tools", [])
    items = payload["input"]
    assert isinstance(tools, list)
    assert isinstance(items, list)
    in_tools = [tool["name"] for tool in tools]
    added = [
        tool["name"]
        for item in items
        if item.get("type") == "additional_tools"
        for tool in item["tools"]
    ]
    return in_tools, added


@pytest.fixture(autouse=True)
def provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.delenv("LLM_FALLBACK_MODEL_ID", raising=False)


async def test_a_routed_call_receives_loaded_tools_in_the_routed_models_format() -> None:
    providers = _Providers()

    await _run(
        providers,
        thread_settings={"model_id": _OPUS, "model_routing_enabled": True},
        workspace_settings=WorkspaceSettings(
            {
                **_MODEL_DEFAULTS,
                "default_agent_routing_balanced_model": _GPT,
                "default_agent_routing_balanced_reasoning_effort": "medium",
            }
        ),
        model_route="balanced",
    )

    assert [sent.model_id for sent in providers.sent] == [_GPT, _GPT]
    in_tools, added = _openai_offer(providers.sent[-1].payload)
    assert added == ["notion-search"]
    assert "notion-search" not in in_tools


async def test_a_fallback_attempt_receives_loaded_tools_in_the_fallback_models_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_FALLBACK_MODEL_ID", _GPT)
    providers = _Providers(failing=ChatAnthropic)

    await _run(providers, thread_settings={"model_id": _OPUS})

    assert [sent.model_id for sent in providers.sent] == [_OPUS, _OPUS, _GPT]
    in_tools, added = _openai_offer(providers.sent[-1].payload)
    assert added == ["notion-search"]
    assert "notion-search" not in in_tools
