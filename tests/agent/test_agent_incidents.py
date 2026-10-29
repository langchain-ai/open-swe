"""Incident runs keep the system-thread runtime and add incident context and tools."""

import json
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from langgraph.types import Command
from runtime_api.operations import RunOperation, ToolEvidence

from openswe.incidents import runtime, service, turns
from openswe.incidents.models import Incident, IncidentPolicy
from openswe.mcp.instance import instance_mcp_source
from openswe.mcp.runtime import _tool_name
from openswe.mcp.workspace import workspace_mcp_source
from openswe.middleware.model_call_shape import CallShape
from openswe.sandboxes.tool_runtime import ToolSurface
from openswe.slack.channels import SlackChannel
from openswe.workspaces.store import DEFAULT_WORKSPACE_SLUG
from tests.agent.test_agent_assembly_context import (
    _plan_agent,
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
    monkeypatch, saved_thread_scope, explicit
):
    from openswe.middleware.dynamic_tools import DynamicToolMiddleware
    from openswe.sandboxes.state import SandboxBackendProxy
    from openswe.server import _registered_tool_name

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
    status_page_tools = [
        StructuredTool.from_function(
            coroutine=update_incident,
            name=_tool_name(connection, "status_page_update"),
            description="Update the public status page",
            metadata={"mcp_tool_name": "status_page_update"},
        )
        for connection in ("incident", "incident_io", "oncall", "incident-" * 10)
    ]
    with patch(
        "openswe.server.load_mcp_tools", AsyncMock(return_value=[remote, *status_page_tools])
    ) as mcps:
        result = cast(dict[str, Any], await _plan_agent(_incident_config()))
    mcps.assert_awaited_once()
    # System runs load the instance tier and the default workspace's, never a person's.
    assert [source.namespace for source in mcps.await_args.args] == [
        instance_mcp_source().namespace,
        workspace_mcp_source(DEFAULT_WORKSPACE_SLUG).namespace,
    ]
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
        from openswe.middleware.exclude_tools import ExcludeToolsMiddleware
        from openswe.server import INCIDENT_AUTOMATIC_EXCLUDED_TOOLS

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
    } <= {type(item).__name__ for item in middleware}
    surface = cast(ToolSurface, result["surface"])
    assert "PullRequestCreationGuardMiddleware" in {
        type(item).__name__ for item in surface.shell_guards
    }
    dynamic = next(item for item in middleware if isinstance(item, DynamicToolMiddleware))
    assert remote.name in dynamic.tools[0].description
    assert {tool.name for tool in await dynamic.catalog_tools()} == {remote.name}
    for tool in status_page_tools:
        assert tool.name not in dynamic.tools[0].description
        load_result = await dynamic.tools[0].ainvoke(
            {
                "name": "load_integration_tools",
                "args": {"tool_names": [tool.name]},
                "id": "status-page-load",
                "type": "tool_call",
            }
        )
        assert isinstance(load_result, Command)
        assert isinstance(load_result.update, dict)
        assert load_result.update["messages"][0].status == "error"
    subagent = result["subagents"][0]
    assert subagent.tools is not None
    assert ("open_pull_request" in subagent.tools) is explicit
    # Incident tools are the parent's alone.
    assert not (runtime.INCIDENT_TOOL_NAMES | {"manage_incident"}) & set(subagent.tools)
    guard = next(item for item in middleware if isinstance(item, runtime.IncidentMiddleware))
    session.check.side_effect = PermissionError("revoked")
    with pytest.raises(PermissionError, match="revoked"):
        await guard.ashape_model_call(CallShape(), {"messages": []}, MagicMock())


async def test_forged_incident_source_on_a_normal_thread_is_refused(saved_thread_scope):
    saved_thread_scope.update(source="slack", visibility="public")
    with pytest.raises(PermissionError, match="not an incident"):
        await _plan_agent(_incident_config())


@pytest.mark.parametrize("requested_action", [False, True])
async def test_main_agent_records_the_incident_report_through_the_tool(
    registry_db, monkeypatch, saved_thread_scope, requested_action
):

    from openswe.agent_api.graph import RunSession
    from openswe.agent_api.runs import in_graph_context

    await service.POLICIES.put(IncidentPolicy(enabled=True, workspace_id="T1"))
    await service.INCIDENTS.put(
        "incident",
        Incident(id="incident", workspace_id="T1", channel_id="C1", thread_id="thread-ctx"),
    )
    saved_thread_scope.update(
        source="incidents_agent", owner_type="system", visibility="public", incident_id="incident"
    )
    monkeypatch.setattr(SlackChannel, "fetch", AsyncMock(return_value=dict(CHANNEL)))
    posted = AsyncMock(return_value="9.0")
    monkeypatch.setattr(runtime, "post_slack_thread_reply_with_ts", posted)
    config = _incident_config(
        **({"incident_request": "Open a PR for the confirmed fix"} if requested_action else {})
    )
    context = turns.context_block("C1", {"ts": "1.0", "user": "U1", "text": "Errors reported"})
    planned = await _plan_agent(config)
    surface = cast(ToolSurface, planned["surface"])
    await surface.prepare()
    session = RunSession(surface, config, None, started_here=True)
    # The Slack context the model is sent is evidence the report can cite.
    state: dict[str, object] = {"messages": [HumanMessage(content=context["text"])]}
    with patch(
        "openswe.server.PrepareAgentRunMiddleware._prepare",
        AsyncMock(return_value={"work_dir": "/workspace", "rendered_system_prompt": "System"}),
    ):
        await in_graph_context(config, lambda runtime: session.loop.model_call(state, runtime))
    if requested_action:
        # The PR the run opened, as its tool result is recorded and cited.
        cited = await session.record_evidence(
            ToolEvidence(
                tool="open_pull_request",
                content=json.dumps({"url": "https://github.com/acme/api/pull/42"}),
            )
        )
        assert isinstance(cited.content, str)
        evidence_id = cited.content.rsplit("Incident evidence: ", 1)[1].split("\n", 1)[0]
        text = "Opened the fix PR"
    else:
        evidence_id, text = "slack:1.0", "Errors reported"

    recorded = await session.operation(
        RunOperation(
            name="record_incident_report",
            arguments={"summary": [{"text": text, "evidence_ids": [evidence_id.strip()]}]},
            request_id="report-call",
        )
    )

    assert recorded.status == "success"
    latest = await service.REPORTS.get("incident")
    assert latest is not None
    if requested_action:
        assert "Opened the fix PR" in latest.report.summary
        assert any(
            item.url == "https://github.com/acme/api/pull/42" for item in latest.report.evidence
        )
    else:
        assert latest.report.summary == "Errors reported [slack:1.0]"
    posted.assert_awaited_once()
    assert posted.await_args.args[:2] == ("C1", "0")
    catalog = session.catalog()
    offered = {spec.name for spec in catalog.operations} - set(catalog.excluded)
    assert {"record_incident_report", "search_incidents"} <= offered
    assert ("open_pull_request" in offered) is requested_action
    assert catalog.incident_evidence
