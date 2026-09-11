"""Incident runs share main-agent assembly without coding capabilities."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agent.incidents import runtime
from agent.incidents.models import IncidentPolicy
from tests.agent.test_agent_assembly_context import (
    _capture_create_deep_agent_kwargs,
)
from tests.agent.test_agent_assembly_context import saved_thread_scope as saved_thread_scope


class ScriptedModel(BaseChatModel):
    responses: list[AIMessage]
    bound_names: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "incident-test"

    def bind_tools(self, tools, **kwargs):
        self.bound_names = [tool.name for tool in tools]
        return self

    def _generate(self, *args, **kwargs):
        raise NotImplementedError

    async def _agenerate(self, messages, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])


async def test_incident_uses_main_models_but_no_sandbox_or_delegation(
    monkeypatch, saved_thread_scope
):
    saved_thread_scope.update(source="incidents_agent", owner_type="system", visibility="public")
    session = SimpleNamespace(
        saved=SimpleNamespace(policy=IncidentPolicy(enabled=True)),
        tools=[],
        prompt="Incident prompt",
        check=AsyncMock(),
    )
    monkeypatch.setattr(runtime, "load_incident_session", AsyncMock(return_value=session))
    config = {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "thread-ctx",
            "source": "incidents_agent",
        }
    }
    result = await _capture_create_deep_agent_kwargs(config)
    assert result["subagents"] == []
    assert result["tools"] == []
    assert type(result["backend"].default).__name__ == "StateBackend"
    assert any(type(item).__name__ == "IncidentMiddleware" for item in result["middleware"])
    assert not any(
        type(item).__name__ == "PrepareAgentRunMiddleware" for item in result["middleware"]
    )
    assert result["make_model_calls"]


async def test_desktop_source_cannot_bypass_saved_incident_scope(saved_thread_scope):
    saved_thread_scope.update(source="incidents_agent", owner_type="system", visibility="public")
    with pytest.raises(PermissionError, match="Incident"):
        await _capture_create_deep_agent_kwargs(
            {
                "configurable": {
                    "__is_for_execution__": True,
                    "thread_id": "thread-ctx",
                    "source": "desktop",
                    "local_project_path": "/tmp",
                }
            }
        )


async def test_incident_compaction_rechecks_access_before_model(monkeypatch):
    from deepagents.backends.state import StateBackend

    from agent.middleware.conversation_offloading import ConversationOffloadingMiddleware

    summarize = AsyncMock(return_value="summary")
    monkeypatch.setattr(ConversationOffloadingMiddleware, "_acreate_summary", summarize)
    session = SimpleNamespace(check=AsyncMock(side_effect=PermissionError("revoked")))
    middleware = runtime.IncidentOffloadingMiddleware(
        ScriptedModel(responses=[]), StateBackend(), session
    )
    with pytest.raises(PermissionError, match="revoked"):
        await middleware._acreate_summary([AIMessage(content="Protected context")])
    summarize.assert_not_awaited()


async def test_main_agent_executes_and_persists_incident_report(
    fake_store, monkeypatch, saved_thread_scope
):
    from deepagents import create_deep_agent
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    from agent.incidents import service, slack
    from agent.incidents.models import Incident, IncidentMessage

    policy = IncidentPolicy(enabled=True, workspace_id="T1")
    await service.POLICIES.put("default", policy)
    saved = runtime.IncidentPass(
        id="pass",
        incident_id="incident",
        thread_id="thread-ctx",
        policy=policy,
        messages=[IncidentMessage(id="m1", ts="1.0", text="Errors reported")],
    )
    await runtime.PASSES.put(saved.id, saved)
    await service.INVESTIGATIONS.put(
        "incident",
        Incident(
            id="incident",
            workspace_id="T1",
            channel_id="C1",
            thread_id="worker",
            agent_thread_id=saved.thread_id,
            active_pass_id=saved.id,
        ),
    )
    saved_thread_scope.update(
        source="incidents_agent", owner_type="system", visibility="public", incident_id="incident"
    )
    monkeypatch.setattr(
        slack,
        "channel_info",
        AsyncMock(
            return_value={
                "id": "C1",
                "name": "inc-test",
                "is_channel": True,
                "is_private": False,
                "is_ext_shared": False,
                "is_pending_ext_shared": False,
                "is_member": True,
            }
        ),
    )
    monkeypatch.setattr(runtime, "load_workspace_mcp_tools", AsyncMock(return_value=[]))
    config = {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": saved.thread_id,
            "source": "incidents_agent",
            "incident_pass_id": saved.id,
        }
    }
    kwargs = await _capture_create_deep_agent_kwargs(config)
    kwargs.pop("make_model_calls")
    model = ScriptedModel(
        responses=[
            AIMessage(
                content='{"summary":[{"text":"Errors reported","evidence_ids":["slack:m1"]}]}'
            )
        ]
    )
    kwargs["model"] = model
    kwargs["checkpointer"] = InMemorySaver()
    kwargs["store"] = InMemoryStore()
    agent = create_deep_agent(**kwargs)
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content="Analyze current incident")]}, config
    )
    report = (await runtime.PASSES.get(saved.id)).report
    assert report is not None
    assert report.summary == "Errors reported [slack:m1]"
    assert result["messages"][-1].type == "ai"
    assert "execute" not in model.bound_names
    assert "task" not in model.bound_names
    assert "search_incidents" in model.bound_names
