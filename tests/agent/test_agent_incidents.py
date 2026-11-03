"""Incident runs keep the system-thread runtime and add incident context and tools."""

import json
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import HumanMessage, messages_to_dict
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.types import Command
from runtime_api.loop import INCIDENT_CONTEXT, IncidentContext, Routed
from runtime_api.operations import (
    OPERATION,
    RECORD_EVIDENCE,
    EvidenceReply,
    OperationResult,
    RunOperation,
    ToolEvidence,
)

from openswe.agent_api.agent import AgentKind
from openswe.agent_api.run_calls import TokenRun
from openswe.agent_api.run_catalog import RunCatalog
from openswe.agent_api.tokens import RemoteRun
from openswe.incidents import runtime, service, turns
from openswe.incidents.models import Incident, IncidentPolicy
from openswe.mcp.instance import instance_mcp_source
from openswe.mcp.runtime import _tool_name
from openswe.mcp.workspace import workspace_mcp_source
from openswe.sandboxes.tool_runtime import ToolSurface
from openswe.slack.channels import SlackChannel
from openswe.workspaces.store import DEFAULT_WORKSPACE_SLUG
from tests.agent.test_agent_assembly_context import (
    _agent_catalog,
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


def _incident_config(**extra: str) -> RunnableConfig:
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
    from openswe.server import INCIDENT_AUTOMATIC_EXCLUDED_TOOLS, _registered_tool_name

    saved_thread_scope.update(source="incidents_agent", owner_type="system", visibility="public")
    session = runtime.IncidentSession(
        Incident(id="incident", workspace_id="T1", channel_id="C1", thread_id="thread-ctx"),
        IncidentPolicy(enabled=True, workspace_id="T1", max_model_calls=7),
        run_id=None,
        explicit_request="Open a fix PR" if explicit else None,
        reply_thread_ts="1.0",
    )
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
        result = await _agent_catalog(_incident_config())
    surface = cast(ToolSurface, result["surface"])
    mcps.assert_awaited_once()
    # System runs load the instance tier and the default workspace's, never a person's.
    assert [source.namespace for source in mcps.await_args.args] == [
        instance_mcp_source().namespace,
        workspace_mcp_source(DEFAULT_WORKSPACE_SLUG).namespace,
    ]
    names = {_registered_tool_name(tool) for tool in surface.base_tools}
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
        assert INCIDENT_AUTOMATIC_EXCLUDED_TOOLS <= surface.excluded
        assert INCIDENT_AUTOMATIC_EXCLUDED_TOOLS <= set(surface.loop.hidden_tools)
    assert not {"save_user_skill", "read_user_settings"} & names
    assert surface.skill_sources == ("/organization-skills/", "/bundled-skills/")
    # MDA asks for the incident's context before each model call, within the policy's budget.
    assert surface.loop.incident
    assert surface.model_call_limit == 7
    dynamic = surface.dynamic
    assert dynamic is not None
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
    subagent = surface.subagents[0]
    assert subagent.tools is not None
    assert ("open_pull_request" in subagent.tools) is explicit
    # Incident tools are the parent's alone.
    assert not (runtime.INCIDENT_TOOL_NAMES | {"manage_incident"}) & set(subagent.tools)


async def test_forged_incident_source_on_a_normal_thread_is_refused(saved_thread_scope):
    saved_thread_scope.update(source="slack", visibility="public")
    with pytest.raises(PermissionError, match="not an incident"):
        await _agent_catalog(_incident_config())


@pytest.mark.parametrize("requested_action", [False, True])
async def test_main_agent_records_the_incident_report_through_the_tool(
    registry_db, monkeypatch, saved_thread_scope, requested_action
):
    """Each call of the run loads its incident afresh, on whichever replica it lands: what
    its context and tool results recorded is the evidence its report may cite."""
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "incident-signing-key-0123456789abcdef")
    await service.POLICIES.put(IncidentPolicy(enabled=True, workspace_id="T1"))
    await service.INCIDENTS.put(
        "incident",
        Incident(id="incident", workspace_id="T1", channel_id="C1", thread_id="thread-ctx"),
    )
    saved_thread_scope.update(
        source="incidents_agent", owner_type="system", visibility="public", incident_id="incident"
    )
    monkeypatch.setattr(SlackChannel, "fetch", AsyncMock(return_value=dict(CHANNEL)))
    monkeypatch.setattr("openswe.agent_api.agent.resolve_github_token", AsyncMock())
    posted = AsyncMock(return_value="9.0")
    monkeypatch.setattr(runtime, "post_slack_thread_reply_with_ts", posted)
    config = _incident_config(
        **({"incident_request": "Open a PR for the confirmed fix"} if requested_action else {})
    )
    stamped = RemoteRun(
        thread_id="thread-ctx", assistant_id="agent", configurable=dict(config["configurable"])
    )
    planned = await _agent_catalog(config)
    surface = cast(ToolSurface, planned["surface"])
    await surface.prepare()
    parts = AgentKind()
    starting = RunCatalog(parts, stamped, surface, config)
    run = stamped.model_copy(update={"settings": starting.settings()})

    # The Slack context the model is sent is evidence the report can cite.
    context = turns.context_block("C1", {"ts": "1.0", "user": "U1", "text": "Errors reported"})
    told = await TokenRun.answer(
        parts,
        run,
        INCIDENT_CONTEXT.name,
        Routed(
            run_id="run-1", humans=messages_to_dict([HumanMessage(content=context["text"])])
        ).model_dump(mode="json"),
    )
    assert IncidentContext.model_validate(told).instructions
    if requested_action:
        # The PR the run opened, as its tool result is recorded and cited.
        cited = EvidenceReply.model_validate(
            await TokenRun.answer(
                parts,
                run,
                RECORD_EVIDENCE.name,
                ToolEvidence(
                    run_id="run-1",
                    tool="open_pull_request",
                    content=json.dumps({"url": "https://github.com/acme/api/pull/42"}),
                ).model_dump(mode="json"),
            )
        )
        assert isinstance(cited.content, str)
        evidence_id = cited.content.rsplit("Incident evidence: ", 1)[1].split("\n", 1)[0]
        text = "Opened the fix PR"
    else:
        evidence_id, text = "slack:1.0", "Errors reported"

    recorded = OperationResult.model_validate(
        await TokenRun.answer(
            parts,
            run,
            OPERATION.name,
            RunOperation(
                run_id="run-1",
                name="record_incident_report",
                arguments={"summary": [{"text": text, "evidence_ids": [evidence_id.strip()]}]},
                request_id="report-call",
                grant=run.grant("record_incident_report"),
            ).model_dump(mode="json"),
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
    catalog = starting.build()
    offered = {spec.name for spec in catalog.operations} - set(catalog.excluded)
    assert {"record_incident_report", "search_incidents"} <= offered
    assert ("open_pull_request" in offered) is requested_action
    assert catalog.incident_evidence
