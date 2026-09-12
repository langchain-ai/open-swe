"""Incident runs retain the system-thread runtime and add incident context."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool

from agent.dashboard.workspace_mcps import workspace_mcp_source
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


@pytest.mark.parametrize("explicit", [False, True])
async def test_incident_uses_system_sandbox_tools_integrations_and_delegation(
    fake_store, monkeypatch, saved_thread_scope, explicit
):
    from agent.middleware.dynamic_tools import DynamicToolMiddleware
    from agent.sandboxes.state import SandboxBackendProxy
    from agent.server import _registered_tool_name

    saved_thread_scope.update(source="incidents_agent", owner_type="system", visibility="public")
    session = runtime.IncidentSession(
        runtime.IncidentPass(
            id="pass",
            incident_id="incident",
            thread_id="thread-ctx",
            policy=IncidentPolicy(enabled=True),
            explicit=explicit,
            question="Open a fix PR" if explicit else None,
        )
    )
    from agent.source_context import SlackThreadRef

    session.slack_thread = SlackThreadRef(channel_id="C1", thread_ts="1.0")
    session.check = AsyncMock()
    monkeypatch.setattr(runtime, "load_incident_session", AsyncMock(return_value=session))

    async def update_incident(status: str) -> str:
        return status

    remote = StructuredTool.from_function(
        coroutine=update_incident, name="incident_update", description="Update incident status"
    )
    config = {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "thread-ctx",
            "source": "incidents_agent",
            "slack_thread": {"channel_id": "C1", "thread_ts": "1.0"},
        }
    }
    with (
        patch("agent.server.load_mcp_tools", AsyncMock(return_value=[remote])) as mcps,
        patch("agent.server._notion_tools_for", AsyncMock(return_value=[])) as notion,
        patch("agent.server.load_browser_tools", return_value=[]) as browser,
    ):
        result = await _capture_create_deep_agent_kwargs(config)
    mcps.assert_awaited_once_with(workspace_mcp_source)
    notion.assert_awaited_once_with(None)
    browser.assert_called_once()
    assert isinstance(result["backend"].default, SandboxBackendProxy)
    names = {_registered_tool_name(tool) for tool in result["tools"]}
    assert {
        "open_pull_request",
        "http_request",
        "background_execute",
        "slack_thread_reply",
        "incidents_read_repo_file",
    } <= names
    assert not {"save_user_instructions", "save_user_skill", "read_user_settings"} & names
    assert result["skills"] == ["/organization-skills/", "/bundled-skills/"]
    middleware = result["middleware"]
    assert {
        "PrepareAgentRunMiddleware",
        "IncidentMiddleware",
        "PullRequestCreationGuardMiddleware",
        "WorkspaceSkillsMiddleware",
    } <= {type(item).__name__ for item in middleware}
    dynamic = next(item for item in middleware if isinstance(item, DynamicToolMiddleware))
    assert remote.name in dynamic.tools[0].description
    subagent = result["subagents"][0]
    assert "open_pull_request" in {_registered_tool_name(tool) for tool in subagent["tools"]}
    guard = next(
        item for item in subagent["middleware"] if isinstance(item, runtime.IncidentMiddleware)
    )
    await guard.aafter_agent({"messages": [AIMessage(content="Subagent findings")]}, None)
    session.check.side_effect = PermissionError("revoked")
    handler = AsyncMock()
    with pytest.raises(PermissionError, match="revoked"):
        await guard.awrap_tool_call(SimpleNamespace(tool_call={"name": "execute"}), handler)
    handler.assert_not_awaited()


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


@pytest.mark.parametrize("requested_action", [False, True])
async def test_main_agent_executes_and_persists_incident_report(
    fake_store, monkeypatch, saved_thread_scope, requested_action
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
        explicit=requested_action,
        question="Open a PR for the confirmed fix" if requested_action else None,
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
    performed = AsyncMock(return_value={"url": "https://github.com/acme/api/pull/42"})
    if requested_action:

        async def open_pull_request() -> dict:
            """Open the requested test PR through a simulated external API."""
            return await performed()

        class ActionModel(ScriptedModel):
            async def _agenerate(self, messages, **kwargs):
                observation = next(
                    (
                        m
                        for m in reversed(messages)
                        if isinstance(m, ToolMessage) and m.tool_call_id == "pr-call"
                    ),
                    None,
                )
                if observation is None:
                    reply = AIMessage(
                        content="",
                        tool_calls=[{"id": "pr-call", "name": "open_pull_request", "args": {}}],
                    )
                else:
                    evidence_id = (
                        observation.text.rsplit("Incident evidence: ", 1)[1]
                        .strip()
                        .split("\n", 1)[0]
                    )
                    reply = AIMessage(
                        content=json.dumps(
                            {
                                "summary": [
                                    {"text": "Opened the fix PR", "evidence_ids": [evidence_id]}
                                ]
                            }
                        )
                    )
                return ChatResult(generations=[ChatGeneration(message=reply)])

        kwargs["tools"] = [
            tool
            for tool in kwargs["tools"]
            if getattr(tool, "__name__", getattr(tool, "name", "")) != "open_pull_request"
        ]
        kwargs["tools"].append(StructuredTool.from_function(coroutine=open_pull_request))
        model = ActionModel(responses=[])
    kwargs["model"] = model
    for subagent in kwargs["subagents"]:
        subagent["model"] = ScriptedModel(responses=[])
    kwargs["checkpointer"] = InMemorySaver()
    kwargs["store"] = InMemoryStore()
    agent = create_deep_agent(**kwargs)
    with patch(
        "agent.server.PrepareAgentRunMiddleware._prepare",
        AsyncMock(
            return_value={
                "work_dir": "/workspace",
                "rendered_system_prompt": "Normal system-thread instructions",
            }
        ),
    ):
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="Analyze current incident")]}, config
        )
    report = (await runtime.PASSES.get(saved.id)).report
    assert report is not None
    if requested_action:
        performed.assert_awaited_once()
        assert "Opened the fix PR" in report.summary
        assert any(item.url == "https://github.com/acme/api/pull/42" for item in report.evidence)
    else:
        performed.assert_not_awaited()
        assert report.summary == "Errors reported [slack:m1]"
    assert result["messages"][-1].type == "ai"
    assert "execute" in model.bound_names
    assert "task" in model.bound_names
    assert "search_incidents" in model.bound_names
