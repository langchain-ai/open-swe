"""Incident conversations use saved ownership and enforce capabilities at execution."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool, ToolException
from langgraph.runtime import Runtime

from agent.dashboard import workspace_mcps
from agent.incidents import documents, providers, runtime, service
from agent.incidents.models import Incident, IncidentMessage, IncidentPolicy, IncidentReport
from agent.incidents.provider_models import ProviderBinding, ProviderSnapshot
from agent.mcp import MCPConnectionUpdate


@pytest.fixture
async def incident_scope(fake_store, monkeypatch):
    import langgraph_sdk

    record = Incident(
        id="incident", workspace_id="T1", channel_id="C1", thread_id="worker", can_read=True
    )
    await service.INVESTIGATIONS.put(record.id, record)
    await service.POLICIES.put("default", IncidentPolicy(enabled=True, workspace_id="T1"))
    metadata = {
        "source": "incidents_agent",
        "owner_type": "system",
        "visibility": "public",
        "incident_id": "incident",
    }
    monkeypatch.setattr(
        langgraph_sdk,
        "get_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(get=AsyncMock(return_value={"metadata": metadata}))
        ),
    )
    return record, metadata


async def test_forged_incident_source_does_not_change_normal_thread(incident_scope):
    from agent.incidents.runtime import load_incident_session

    _, metadata = incident_scope
    metadata.clear()
    with pytest.raises(PermissionError, match="binding"):
        await load_incident_session(
            {
                "configurable": {
                    "thread_id": "normal",
                    "source": "incidents_agent",
                    "incident_pass_id": "forged",
                }
            }
        )


async def test_normal_saved_thread_has_no_incident_capabilities(incident_scope):
    from agent.incidents.runtime import load_incident_session

    _, metadata = incident_scope
    metadata.clear()
    assert await load_incident_session({"configurable": {"thread_id": "normal"}}) is None


async def test_incident_thread_requires_system_ownership_and_saved_pass(incident_scope):
    from agent.incidents.runtime import load_incident_session

    _, metadata = incident_scope
    metadata["owner_type"] = "user"
    with pytest.raises(PermissionError):
        await load_incident_session(
            {"configurable": {"thread_id": "conversation", "incident_pass_id": "forged"}}
        )


async def test_incident_middleware_blocks_fabricated_coding_tool():
    from agent.incidents.runtime import IncidentMiddleware

    session = SimpleNamespace(check=AsyncMock(), tools=[], prompt="Incident prompt")
    middleware = IncidentMiddleware(session)
    handler = AsyncMock()
    with pytest.raises(PermissionError, match="not available"):
        await middleware.awrap_tool_call(
            SimpleNamespace(tool_call={"name": "execute", "args": {"command": "echo bad"}}), handler
        )
    handler.assert_not_awaited()


async def test_revocation_latches_even_when_later_check_succeeds():
    from agent.incidents.runtime import IncidentMiddleware

    check = AsyncMock(side_effect=[PermissionError("revoked"), None])
    middleware = IncidentMiddleware(SimpleNamespace(check=check, tools=[], prompt="prompt"))
    handler = AsyncMock()
    for _ in range(2):
        with pytest.raises(PermissionError, match="revoked"):
            await middleware.awrap_model_call(SimpleNamespace(), handler)
    handler.assert_not_awaited()


@pytest.fixture
async def live_pass(incident_scope, monkeypatch):
    record, metadata = incident_scope
    record.agent_thread_id = "conversation"
    record.active_pass_id = "pass-1"
    record.status = "investigating"
    await service.INVESTIGATIONS.put(record.id, record)
    policy = await service.get_policy()
    saved = runtime.IncidentPass(
        id="pass-1",
        incident_id=record.id,
        thread_id="conversation",
        policy=policy,
        messages=[IncidentMessage(id="1", ts="1", text="Responders reported errors", user="U1")],
    )
    await runtime.PASSES.put(saved.id, saved)
    info = {
        "id": "C1",
        "name": "inc-api",
        "is_channel": True,
        "is_member": True,
        "is_private": False,
        "is_ext_shared": False,
        "is_pending_ext_shared": False,
    }
    monkeypatch.setattr(runtime.slack, "channel_info", AsyncMock(return_value=info))
    monkeypatch.setattr(runtime, "load_workspace_mcp_tools", AsyncMock(return_value=[]))
    return SimpleNamespace(
        record=record,
        metadata=metadata,
        saved=saved,
        policy=policy,
        info=info,
        config={"configurable": {"thread_id": "conversation", "incident_pass_id": saved.id}},
    )


async def test_valid_saved_system_pass_loads_live_scope_and_finalizes_cited_report(live_pass):
    session = await runtime.load_incident_session(live_pass.config)
    assert session is not None
    assert session.saved.id == "pass-1"
    assert [item.id for item in session.collector.evidence] == ["slack:1"]
    await runtime.IncidentMiddleware(session).aafter_agent(
        {
            "messages": [
                AIMessage(
                    content=json.dumps(
                        {
                            "summary": [
                                {"text": "Responders reported errors", "evidence_ids": ["slack:1"]}
                            ],
                        }
                    )
                )
            ]
        },
        Runtime(),
    )
    saved = await runtime.PASSES.get("pass-1")
    assert saved.report.id == "pass-1"
    assert saved.report.summary == "Responders reported errors [slack:1]"


@pytest.mark.parametrize("change", ["membership", "excluded"])
async def test_loaded_pass_immediately_loses_access_after_slack_or_policy_revocation(
    live_pass, change
):
    session = await runtime.load_incident_session(live_pass.config)
    if change == "membership":
        live_pass.info["is_member"] = False
    else:
        live_pass.policy.excluded_channel_ids = ["C1"]
        await service.POLICIES.put("default", live_pass.policy)
    with pytest.raises(PermissionError):
        await session.check()
    with pytest.raises(PermissionError):
        await runtime.load_incident_session(live_pass.config)


@pytest.mark.parametrize(
    "identity",
    [
        {"github_login": "responder"},
        {"user_email": "responder@example.com"},
        {"local_run": True},
        {"source": "desktop"},
    ],
)
async def test_valid_binding_cannot_borrow_personal_execution_identity(live_pass, identity):
    config = {"configurable": {**live_pass.config["configurable"], **identity}}
    with pytest.raises(PermissionError, match="personal"):
        await runtime.load_incident_session(config)


async def test_provider_revision_change_revokes_loaded_analysis(live_pass, monkeypatch):
    await workspace_mcps.save_workspace_mcp(
        "incident",
        MCPConnectionUpdate(
            name="incident", url="https://mcp.incident.io/mcp", allowed_tools=["incident_show"]
        ),
    )
    await providers.BINDINGS.put(
        live_pass.record.id,
        ProviderBinding(
            incident_id=live_pass.record.id,
            workspace_id="T1",
            channel_id="C1",
            connection_name="incident",
            external_id="INC1",
            snapshot=ProviderSnapshot(external_id="INC1", slack_channel_id="C1"),
        ),
    )

    async def read_provider(incident_id: str):
        return [], {
            "structured_content": {"incident": {"id": incident_id, "slack_channel_id": "C1"}}
        }

    remote = StructuredTool.from_function(
        coroutine=read_provider,
        name="incident_show",
        description="Read provider incident",
        args_schema={
            "type": "object",
            "properties": {"incident_id": {"type": "string"}},
            "required": ["incident_id"],
        },
        metadata={"mcp_tool_name": "incident_show"},
        response_format="content_and_artifact",
    )
    monkeypatch.setattr(providers, "load_mcp_tools", AsyncMock(return_value=[remote]))
    await providers.refresh_provider(live_pass.record)
    assert (await providers.BINDINGS.get(live_pass.record.id)).error is None
    live_pass.saved.provider_scope = await providers.analysis_scope(live_pass.record)
    await runtime.PASSES.put(live_pass.saved.id, live_pass.saved)
    session = await runtime.load_incident_session(live_pass.config)

    await workspace_mcps.save_workspace_mcp(
        "incident",
        MCPConnectionUpdate(
            name="incident", url="https://mcp.incident.io/mcp", allowed_tools=["incident_show"]
        ),
    )
    with pytest.raises(PermissionError):
        await session.check()


@pytest.mark.parametrize("change", ["revision", "disabled", "tool_removed"])
async def test_evidence_connection_changes_revoke_loaded_pass(live_pass, change):
    await workspace_mcps.save_workspace_mcp(
        "telemetry",
        MCPConnectionUpdate(
            name="telemetry",
            url="https://telemetry.example.com/mcp",
            allowed_tools=["search_datadog_metrics"],
        ),
    )
    live_pass.saved.evidence_scope = await runtime.evidence_scope()
    assert live_pass.saved.evidence_scope
    await runtime.PASSES.put(live_pass.saved.id, live_pass.saved)
    session = await runtime.load_incident_session(live_pass.config)

    await workspace_mcps.save_workspace_mcp(
        "telemetry",
        MCPConnectionUpdate(
            name="telemetry",
            url="https://telemetry.example.com/mcp",
            enabled=change != "disabled",
            allowed_tools=[] if change == "tool_removed" else ["search_datadog_metrics"],
        ),
    )
    with pytest.raises(PermissionError, match="evidence"):
        await session.check()


def telemetry_tool(mode):
    async def read(query: str):
        if mode == "error":
            raise ToolException("Telemetry request failed")
        if mode == "artifact_error":
            return "Telemetry request failed", {"isError": True}
        if mode == "structured_error":
            return [], {"structured_content": {"isError": True, "error": "Request failed"}}
        if mode == "structured":
            return [], {"structured_content": {"error_rate": 0.18, "query": query}}
        return [{"type": "text", "text": "API error rate is 18%"}], None

    return StructuredTool.from_function(
        coroutine=read,
        name="mcp_telemetry_metrics",
        description="Read telemetry",
        metadata={"mcp_tool_name": "search_datadog_metrics"},
        response_format="content_and_artifact",
        handle_tool_error=True,
    )


@pytest.mark.parametrize("mode", ["text", "structured"])
async def test_mcp_evidence_preserves_successful_observations(live_pass, monkeypatch, mode):
    remote = telemetry_tool(mode)
    monkeypatch.setattr(runtime, "load_workspace_mcp_tools", AsyncMock(return_value=[remote]))
    session = await runtime.load_incident_session(live_pass.config)
    tool = next(tool for tool in session.tools if tool.name == remote.name)
    result = await tool.ainvoke({"query": "service:api"})
    assert "evidence_id" in result
    assert ("18%" if mode == "text" else "0.18") in result["observation"]
    evidence = next(item for item in session.collector.evidence if item.id == result["evidence_id"])
    assert evidence.source == "mcp"
    assert evidence.query is not None
    assert "service:api" in evidence.query


@pytest.mark.parametrize("mode", ["error", "artifact_error", "structured_error"])
async def test_mcp_errors_become_coverage_gaps_instead_of_evidence(live_pass, monkeypatch, mode):
    remote = telemetry_tool(mode)
    monkeypatch.setattr(runtime, "load_workspace_mcp_tools", AsyncMock(return_value=[remote]))
    session = await runtime.load_incident_session(live_pass.config)
    tool = next(tool for tool in session.tools if tool.name == remote.name)
    result = await tool.ainvoke({"query": "service:api"})
    assert "gap" in result
    assert not any(evidence.source == "mcp" for evidence in session.collector.evidence)


@pytest.mark.parametrize("change", ["cancelled", "replaced", "membership"])
async def test_final_report_is_not_saved_after_pass_or_channel_revocation(live_pass, change):
    session = await runtime.load_incident_session(live_pass.config)
    if change == "cancelled":
        live_pass.saved.cancelled = True
        await runtime.PASSES.put(live_pass.saved.id, live_pass.saved)
    elif change == "replaced":
        live_pass.record.active_pass_id = "new-pass"
        await service.INVESTIGATIONS.put(live_pass.record.id, live_pass.record)
    else:
        live_pass.info["is_member"] = False
    with pytest.raises(PermissionError):
        await runtime.IncidentMiddleware(session).aafter_agent(
            {
                "messages": [
                    AIMessage(
                        content=json.dumps(
                            {
                                "summary": [
                                    {"text": "Stale conclusion", "evidence_ids": ["slack:1"]}
                                ],
                            }
                        )
                    )
                ]
            },
            Runtime(),
        )
    assert (await runtime.PASSES.get(live_pass.saved.id)).report is None


async def test_reading_related_incident_tracks_access_dependency_for_later_output(
    live_pass, monkeypatch
):
    related_info = {**live_pass.info, "id": "C2", "name": "inc-earlier"}

    async def channel_info(channel_id):
        return live_pass.info if channel_id == "C1" else related_info

    monkeypatch.setattr(runtime.slack, "channel_info", channel_info)
    related = Incident(
        id="related",
        workspace_id="T1",
        channel_id="C2",
        thread_id="related-worker",
        status="completed",
    )
    await service.INVESTIGATIONS.put(related.id, related)
    await documents.update_from_report(
        related,
        IncidentReport(summary="Previous incident involved connection saturation"),
        expected_revision=0,
        run_id="historical-pass",
    )
    session = await runtime.load_incident_session(live_pass.config)
    read = next(tool for tool in session.tools if tool.name == "read_incident")
    result = await read.ainvoke({"incident_id": related.id})
    assert "Previous incident involved connection saturation" in result["observation"]
    assert related.id in (await runtime.PASSES.get(live_pass.saved.id)).related_incident_ids
    await session.check()

    related_info["is_member"] = False
    with pytest.raises(HTTPException):
        await session.check()
    with pytest.raises(HTTPException):
        await runtime.IncidentMiddleware(session).aafter_agent(
            {
                "messages": [
                    AIMessage(
                        content=json.dumps(
                            {
                                "summary": [
                                    {
                                        "text": "Similar to the earlier incident",
                                        "evidence_ids": [result["evidence_id"]],
                                    }
                                ],
                            }
                        )
                    )
                ]
            },
            Runtime(),
        )
    assert (await runtime.PASSES.get(live_pass.saved.id)).report is None
