"""Incident runs keep the system-thread runtime and add incident context and tools."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents.middleware.types import ModelRequest, ToolCallRequest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool

from agent.incidents import runtime, service, turns
from agent.incidents.models import Incident, IncidentPolicy
from agent.mcp.instance import instance_mcp_source
from agent.mcp.workspace import workspace_mcp_source
from agent.slack.channels import SlackChannel
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG
from tests.agent.test_agent_assembly_context import (
    _capture_create_deep_agent_kwargs,
)
from tests.agent.test_agent_assembly_context import saved_thread_scope as saved_thread_scope

CHANNEL = {
    "id": "C1",
    "name": "inc-test",
    "is_channel": True,
    "is_private": False,
    "is_ext_shared": False,
    "is_pending_ext_shared": False,
    "is_member": True,
}


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

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])


def _incident_config(**extra: Any) -> dict[str, Any]:
    return {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "thread-ctx",
            "source": "incidents_agent",
            "incident_id": "incident",
            **extra,
        }
    }


@pytest.mark.parametrize("explicit", [False, True])
async def test_incident_uses_system_sandbox_tools_integrations_and_delegation(
    fake_store, monkeypatch, saved_thread_scope, explicit
):
    from agent.middleware.dynamic_tools import DynamicToolMiddleware
    from agent.sandboxes.state import SandboxBackendProxy
    from agent.server import _registered_tool_name

    saved_thread_scope.update(source="incidents_agent", owner_type="system", visibility="public")
    session = runtime.IncidentSession(
        Incident(id="incident", workspace_id="T1", channel_id="C1", thread_id="thread-ctx"),
        IncidentPolicy(enabled=True, workspace_id="T1"),
        explicit_request="Open a fix PR" if explicit else None,
        reply_thread_ts="1.0",
    )
    session.check = AsyncMock()
    monkeypatch.setattr(runtime, "load_incident_session", AsyncMock(return_value=session))

    async def update_incident(status: str) -> str:
        return status

    remote = StructuredTool.from_function(
        coroutine=update_incident, name="incident_update", description="Update incident status"
    )
    with (
        patch("agent.server.load_mcp_tools", AsyncMock(return_value=[remote])) as mcps,
        patch("agent.server._notion_tools_for", AsyncMock(return_value=[])) as notion,
    ):
        result = cast(dict[str, Any], await _capture_create_deep_agent_kwargs(_incident_config()))
    mcps.assert_awaited_once()
    # System runs load the instance tier and the default workspace's, never a person's.
    assert [source.namespace for source in mcps.await_args.args] == [
        instance_mcp_source().namespace,
        workspace_mcp_source(DEFAULT_WORKSPACE_SLUG).namespace,
    ]
    notion.assert_awaited_once_with(None)
    assert isinstance(result["backend"].default, SandboxBackendProxy)
    names = {_registered_tool_name(tool) for tool in result["tools"]}
    write_tools = {
        "open_pull_request",
        "http_request",
        "background_execute",
        "slack_reply",
        "manage_incident",
    }
    assert {"record_incident_report", "search_incidents"} <= names
    if explicit:
        assert write_tools <= names
    else:
        assert not write_tools & names
        from agent.middleware.exclude_tools import ExcludeToolsMiddleware
        from agent.server import INCIDENT_AUTOMATIC_EXCLUDED_TOOLS

        excluded = next(
            item for item in result["middleware"] if isinstance(item, ExcludeToolsMiddleware)
        )._excluded
        assert INCIDENT_AUTOMATIC_EXCLUDED_TOOLS <= excluded
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
    subagent_names = {_registered_tool_name(tool) for tool in subagent["tools"]}
    assert ("open_pull_request" in subagent_names) is explicit
    tool_guard = next(item for item in subagent["middleware"] if item.name == "_SubagentToolGuard")
    tool_handler = AsyncMock()
    for name in runtime.INCIDENT_TOOL_NAMES | {"manage_incident"}:
        request = MagicMock(spec=ToolCallRequest)
        request.tool_call = {"name": name, "args": {}, "id": name, "type": "tool_call"}
        response = await tool_guard.awrap_tool_call(request, tool_handler)
        assert isinstance(response, ToolMessage)
        assert response.tool_call_id == name
        assert "inside a subagent" in response.content
    tool_handler.assert_not_awaited()
    guard = next(
        item for item in subagent["middleware"] if isinstance(item, runtime.IncidentMiddleware)
    )
    session.check.side_effect = PermissionError("revoked")
    handler = AsyncMock()
    with pytest.raises(PermissionError, match="revoked"):
        await guard.awrap_model_call(
            ModelRequest(model=MagicMock(), messages=[], tools=[], state={"messages": []}), handler
        )
    handler.assert_not_awaited()


async def test_forged_incident_source_on_a_normal_thread_is_refused(saved_thread_scope):
    saved_thread_scope.update(source="slack", visibility="public")
    with pytest.raises(PermissionError, match="not an incident"):
        await _capture_create_deep_agent_kwargs(_incident_config())


@pytest.mark.parametrize("requested_action", [False, True])
async def test_main_agent_records_the_incident_report_through_the_tool(
    fake_store, monkeypatch, saved_thread_scope, requested_action
):
    from deepagents import create_deep_agent
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    await service.POLICIES.put("default", IncidentPolicy(enabled=True, workspace_id="T1"))
    await service.INCIDENTS.put(
        "incident",
        Incident(id="incident", workspace_id="T1", channel_id="C1", thread_id="thread-ctx"),
    )
    saved_thread_scope.update(
        source="incidents_agent", owner_type="system", visibility="public", incident_id="incident"
    )
    monkeypatch.setattr(SlackChannel, "fetch", AsyncMock(return_value=dict(CHANNEL)))
    posted = AsyncMock(return_value=("9.0", None))
    monkeypatch.setattr(runtime, "post_slack_thread_reply_with_ts", posted)
    # The scripted model ends its turn in plain text, so the reply requirement
    # posts on its behalf; that path is covered in its own suite.
    monkeypatch.setattr(
        "agent.slack.tools.reply.slack_reply", AsyncMock(return_value={"success": True})
    )
    config = _incident_config(
        **({"incident_request": "Open a PR for the confirmed fix"} if requested_action else {})
    )
    kwargs = cast(dict[str, Any], await _capture_create_deep_agent_kwargs(config))
    kwargs.pop("make_model_calls")
    context = turns.context_block("C1", {"ts": "1.0", "user": "U1", "text": "Errors reported"})
    performed = AsyncMock(return_value={"url": "https://github.com/acme/api/pull/42"})

    def report_call(evidence_id: str, text: str) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "report-call",
                    "name": "record_incident_report",
                    "args": {"summary": [{"text": text, "evidence_ids": [evidence_id]}]},
                }
            ],
        )

    class TurnModel(ScriptedModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            tool_results = [m for m in messages if isinstance(m, ToolMessage)]
            if any(m.tool_call_id == "report-call" for m in tool_results):
                reply = AIMessage(content="Recorded.")
            elif requested_action and not tool_results:
                reply = AIMessage(
                    content="",
                    tool_calls=[{"id": "pr-call", "name": "open_pull_request", "args": {}}],
                )
            elif requested_action:
                observation = next(m for m in tool_results if m.tool_call_id == "pr-call")
                evidence_id = (
                    observation.text.rsplit("Incident evidence: ", 1)[1].strip().split("\n", 1)[0]
                )
                reply = report_call(evidence_id, "Opened the fix PR")
            else:
                reply = report_call("slack:1.0", "Errors reported")
            return ChatResult(generations=[ChatGeneration(message=reply)])

    if requested_action:

        async def open_pull_request() -> dict:
            """Open the requested test PR through a simulated external API."""
            return await performed()

        kwargs["tools"] = [
            tool
            for tool in kwargs["tools"]
            if getattr(tool, "__name__", getattr(tool, "name", "")) != "open_pull_request"
        ]
        kwargs["tools"].append(StructuredTool.from_function(coroutine=open_pull_request))
    model = TurnModel(responses=[])
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
        result = await agent.ainvoke({"messages": [HumanMessage(content=context["text"])]}, config)

    latest = await service.REPORTS.get("incident")
    assert latest is not None
    if requested_action:
        performed.assert_awaited_once()
        assert "Opened the fix PR" in latest.report.summary
        assert any(
            item.url == "https://github.com/acme/api/pull/42" for item in latest.report.evidence
        )
    else:
        performed.assert_not_awaited()
        assert latest.report.summary == "Errors reported [slack:1.0]"
    posted.assert_awaited_once()
    assert posted.await_args.args[:2] == ("C1", "0")
    assert result["messages"][-1].type == "ai"
    bound = set(model.bound_names)
    assert {"execute", "record_incident_report", "search_incidents"} <= bound
    assert ("task" in bound) is requested_action
    assert ("open_pull_request" in bound) is requested_action
