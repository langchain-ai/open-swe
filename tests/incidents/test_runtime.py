"""Incident conversations use saved ownership and enforce capabilities at execution."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool, ToolException
from langgraph.runtime import Runtime
from langgraph.types import Command

from agent.dashboard import workspace_mcps
from agent.incidents import documents, runtime, service
from agent.incidents.models import Incident, IncidentMessage, IncidentPolicy, IncidentReport
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


async def test_incident_context_preserves_system_instructions_and_full_tools(live_pass):
    live_pass.saved.explicit = True
    live_pass.saved.question = "Open a PR for the retry fix"
    await runtime.PASSES.put(live_pass.saved.id, live_pass.saved)
    session = await runtime.load_incident_session(live_pass.config)
    request = ModelRequest(
        model=MagicMock(),
        messages=[],
        tools=[{"name": "execute"}, {"name": "open_pull_request"}],
        system_message=SystemMessage(content="Normal system instructions"),
        state={"messages": []},
    )
    handler = AsyncMock()
    await runtime.IncidentMiddleware(session).awrap_model_call(request, handler)
    actual = handler.call_args.args[0]
    assert actual.tools == request.tools
    assert actual.system_message.text.startswith("Normal system instructions")
    assert session.saved.question in actual.system_message.text


@pytest.mark.parametrize("explicit", [False, True])
async def test_only_current_explicit_request_is_presented_as_authorized(live_pass, explicit):
    live_pass.saved.explicit = explicit
    live_pass.saved.question = "Open the requested PR"
    await runtime.PASSES.put(live_pass.saved.id, live_pass.saved)
    session = await runtime.load_incident_session(live_pass.config)
    request = ModelRequest(model=MagicMock(), messages=[], tools=[], state={"messages": []})
    handler = AsyncMock()
    await runtime.IncidentMiddleware(session).awrap_model_call(request, handler)
    text = handler.call_args.args[0].system_message.text
    assert (live_pass.saved.question in text) is explicit


async def test_system_tool_result_is_citable_without_losing_command_updates(live_pass):
    session = await runtime.load_incident_session(live_pass.config)
    middleware = runtime.IncidentMiddleware(session)
    original = ToolMessage(
        content='{"url":"https://github.com/acme/api/pull/42"}',
        tool_call_id="pr-call",
        name="open_pull_request",
    )
    command = Command(update={"messages": [original], "pr_number": 42})
    handler = AsyncMock(return_value=command)
    result = await middleware.awrap_tool_call(
        SimpleNamespace(
            tool_call={
                "id": "pr-call",
                "name": "open_pull_request",
                "args": {"title": "Fix retries"},
            }
        ),
        handler,
    )
    handler.assert_awaited_once()
    assert result.update["pr_number"] == 42
    message = result.update["messages"][0]
    assert message.tool_call_id == "pr-call"
    assert original.content in message.content
    evidence = next(item for item in session.collector.evidence if item.source == "tool")
    assert evidence.id in message.content
    assert evidence.url == "https://github.com/acme/api/pull/42"
    await middleware.aafter_agent(
        {
            "messages": [
                AIMessage(
                    content=json.dumps(
                        {"summary": [{"text": "Opened the fix PR", "evidence_ids": [evidence.id]}]}
                    )
                )
            ]
        },
        Runtime(),
    )
    assert "Opened the fix PR" in (await runtime.PASSES.get(live_pass.saved.id)).report.summary


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


@pytest.mark.parametrize("change", ["revision", "disabled", "tool_removed"])
async def test_evidence_connection_changes_revoke_loaded_pass(live_pass, change):
    await workspace_mcps.save_workspace_mcp(
        "telemetry",
        MCPConnectionUpdate(
            name="telemetry",
            url="https://telemetry.example.com/mcp",
            allowed_tools=["incident_update"],
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
            allowed_tools=[] if change == "tool_removed" else ["incident_update"],
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
        if mode == "structured_with_text":
            return "Query completed", {"structured_content": {"error_rate": 0.18, "query": query}}
        return [{"type": "text", "text": "API error rate is 18%"}], None

    return StructuredTool.from_function(
        coroutine=read,
        name="mcp_telemetry_metrics",
        description="Read telemetry",
        metadata={"mcp_tool_name": "search_datadog_metrics"},
        response_format="content_and_artifact",
        handle_tool_error=True,
    )


@pytest.mark.parametrize("mode", ["text", "structured", "structured_with_text"])
async def test_mcp_evidence_preserves_successful_observations(live_pass, mode):
    remote = telemetry_tool(mode)
    session = await runtime.load_incident_session(live_pass.config)
    call = {
        "type": "tool_call",
        "id": "metrics-call",
        "name": remote.name,
        "args": {"query": "service:api"},
    }
    result = await runtime.IncidentMiddleware(session).awrap_tool_call(
        SimpleNamespace(tool_call=call), AsyncMock(return_value=await remote.ainvoke(call))
    )
    evidence = next(item for item in session.collector.evidence if item.source == "tool")
    assert evidence.id in str(result.content)
    assert ("18%" if mode == "text" else "0.18") in str(result.content)
    assert evidence.query is not None and "service:api" in evidence.query


@pytest.mark.parametrize("mode", ["error", "artifact_error", "structured_error"])
async def test_mcp_errors_are_not_successful_evidence(live_pass, mode):
    remote = telemetry_tool(mode)
    session = await runtime.load_incident_session(live_pass.config)
    call = {
        "type": "tool_call",
        "id": "metrics-call",
        "name": remote.name,
        "args": {"query": "service:api"},
    }
    original = await remote.ainvoke(call)
    result = await runtime.IncidentMiddleware(session).awrap_tool_call(
        SimpleNamespace(tool_call=call), AsyncMock(return_value=original)
    )
    assert result == original
    assert not any(item.source == "tool" for item in session.collector.evidence)
    assert session.collector.gaps


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


async def test_slack_tools_use_the_saved_incident_destination(live_pass):
    live_pass.record.anchor_ts = "123.456"
    await service.INVESTIGATIONS.put(live_pass.record.id, live_pass.record)
    config = {
        "configurable": {
            **live_pass.config["configurable"],
            "slack_thread": {"channel_id": "ATTACKER", "thread_ts": "999"},
        }
    }
    session = await runtime.load_incident_session(config)
    assert session.slack_thread.channel_id == "C1"
    assert session.slack_thread.thread_ts == "123.456"
