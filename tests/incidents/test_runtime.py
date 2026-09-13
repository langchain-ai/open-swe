"""Incident session loading, middleware, and the report tool on the main agent."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import langgraph_sdk
import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import HumanMessage, ToolMessage

from agent.incidents import documents, runtime, service
from agent.incidents.models import Incident, IncidentPolicy
from agent.incidents.report import CONTEXT_MARKER

CHANNEL = {
    "id": "C1",
    "name": "inc-api",
    "is_channel": True,
    "is_private": False,
    "is_ext_shared": False,
    "is_pending_ext_shared": False,
    "is_member": True,
}


@pytest.fixture
async def incident(fake_store, monkeypatch):
    policy = IncidentPolicy(enabled=True, workspace_id="T1", slack_app_id="A1")
    await service.POLICIES.put("default", policy)
    record = Incident(id="incident", workspace_id="T1", channel_id="C1", thread_id="thread-ctx")
    await service.INCIDENTS.put(record.id, record)
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
            threads=SimpleNamespace(get=AsyncMock(side_effect=lambda _id: {"metadata": metadata}))
        ),
    )
    monkeypatch.setattr(service, "get_slack_channel_info", AsyncMock(return_value=dict(CHANNEL)))
    monkeypatch.setattr(
        runtime, "post_slack_thread_reply_with_ts", AsyncMock(return_value=("7.0", None))
    )
    return SimpleNamespace(record=record, policy=policy, metadata=metadata)


def config(**configurable) -> dict:
    return {
        "configurable": {"thread_id": "thread-ctx", "source": "incidents_agent", **configurable}
    }


def context_message(ts: str = "1.0") -> HumanMessage:
    header = json.dumps(
        {
            "evidence_id": f"slack:{ts}",
            "source_url": f"https://slack.com/archives/C1/p{ts.replace('.', '')}",
        }
    )
    return HumanMessage(content=f"<content>{CONTEXT_MARKER}{header}\nErrors reported</content>")


def request(*messages) -> ModelRequest:
    return ModelRequest(
        model=MagicMock(), messages=list(messages), tools=[], state={"messages": []}
    )


async def test_forged_source_on_a_normal_thread_is_refused(incident):
    incident.metadata.update(source="slack", owner_type="user")
    with pytest.raises(PermissionError, match="not an incident"):
        await runtime.load_incident_session(config())


@pytest.mark.parametrize("change", [{"owner_type": "user"}, {"visibility": "private"}])
async def test_incident_threads_must_be_system_owned_and_public(incident, change):
    incident.metadata.update(change)
    with pytest.raises(PermissionError, match="system owned"):
        await runtime.load_incident_session(config())


@pytest.mark.parametrize(
    "identity", [{"github_login": "octocat"}, {"user_email": "o@x.dev"}, {"local_run": True}]
)
async def test_incident_runs_cannot_carry_personal_identity(incident, identity):
    with pytest.raises(PermissionError, match="personal"):
        await runtime.load_incident_session(config(**identity))


async def test_binding_must_match_the_saved_incident(incident):
    incident.record.thread_id = "other-thread"
    await service.INCIDENTS.put(incident.record.id, incident.record)
    with pytest.raises(PermissionError, match="binding"):
        await runtime.load_incident_session(config())
    incident.metadata["incident_id"] = "missing"
    with pytest.raises(PermissionError, match="binding"):
        await runtime.load_incident_session(config())


async def test_valid_load_exposes_destination_request_and_tools(incident):
    session = await runtime.load_incident_session(
        config(
            incident_request=" Why 500s? ",
            slack_thread={"channel_id": "C1", "reply_thread_ts": "4.0"},
        )
    )
    assert session.explicit_request == "Why 500s?"
    assert (session.slack_thread.channel_id, session.slack_thread.thread_ts) == ("C1", "0")
    assert session.slack_thread.reply_thread_ts == "4.0"
    assert {tool.name for tool in session.tools} == runtime.INCIDENT_TOOL_NAMES
    assert "Why 500s?" in session.instructions


async def test_middleware_injects_prompt_and_registers_context_evidence(incident):
    session = await runtime.load_incident_session(config())
    middleware = runtime.IncidentMiddleware(session)
    handler = AsyncMock(return_value="response")

    result = await middleware.awrap_model_call(request(context_message("1.0")), handler)

    assert result == "response"
    prompt = handler.await_args.args[0].system_message.text
    assert "record_incident_report" in prompt and "null means automatic" in prompt
    assert [item.id for item in session.collector.evidence] == ["slack:1.0"]


async def test_paused_incidents_stop_automatic_turns_but_answer_questions(incident):
    incident.record.status = "paused"
    await service.INCIDENTS.put(incident.record.id, incident.record)
    with pytest.raises(PermissionError, match="not watching"):
        await runtime.load_incident_session(config())
    session = await runtime.load_incident_session(config(incident_request="status?"))
    assert session.explicit_request == "status?"

    await service.POLICIES.put("default", IncidentPolicy(enabled=False, workspace_id="T1"))
    with pytest.raises(PermissionError, match="disabled"):
        await runtime.IncidentMiddleware(session).awrap_model_call(request(), AsyncMock())


async def test_tool_results_become_evidence_and_failures_become_gaps(incident):
    session = await runtime.load_incident_session(config())
    middleware = runtime.IncidentMiddleware(session)
    call = SimpleNamespace(
        tool_call={"id": "pr", "name": "open_pull_request", "args": {"title": "Fix"}}
    )

    success = await middleware.awrap_tool_call(
        call,
        AsyncMock(
            return_value=ToolMessage(
                content='{"url":"https://github.com/acme/api/pull/42"}', tool_call_id="pr"
            )
        ),
    )
    failure = await middleware.awrap_tool_call(
        call, AsyncMock(return_value=ToolMessage(content="boom", tool_call_id="pr", status="error"))
    )

    assert "Incident evidence: tool:" in success.text
    assert session.collector.evidence[0].url == "https://github.com/acme/api/pull/42"
    assert failure.text == "boom"
    assert session.collector.gaps == ["Tool open_pull_request did not confirm a successful result."]


async def test_report_tool_records_posts_and_dedupes_by_digest(incident):
    session = await runtime.load_incident_session(config())
    await runtime.IncidentMiddleware(session).awrap_model_call(
        request(context_message("1.0")), AsyncMock()
    )
    draft = {
        "summary": [
            {"text": "Errors reported", "evidence_ids": ["slack:1.0"]},
            {"text": "Invented cause", "evidence_ids": ["nope"]},
        ]
    }

    first = await session._record_incident_report(**draft)
    second = await session._record_incident_report(**draft)

    latest = await service.REPORTS.get("incident")
    assert latest.report.summary == "Errors reported [slack:1.0]"
    assert first == {"recorded": True, "posted": True, "omitted_claims": True}
    assert second["posted"] is False
    runtime.post_slack_thread_reply_with_ts.assert_awaited_once()
    assert runtime.post_slack_thread_reply_with_ts.await_args.args[:2] == ("C1", "0")
    postmortem = (await documents.document_context("incident"))["postmortem"]["markdown"]
    assert "Errors reported" in postmortem
    assert latest.activity[-1].summary == "Errors reported [slack:1.0]"


async def test_explicit_answers_always_post_into_their_thread(incident):
    session = await runtime.load_incident_session(
        config(
            incident_request="status?", slack_thread={"channel_id": "C1", "reply_thread_ts": "4.0"}
        )
    )
    await runtime.IncidentMiddleware(session).awrap_model_call(
        request(context_message("2.0")), AsyncMock()
    )
    draft = {"summary": [{"text": "Still degraded", "evidence_ids": ["slack:2.0"]}]}

    await session._record_incident_report(**draft)
    await session._record_incident_report(**draft)

    assert runtime.post_slack_thread_reply_with_ts.await_count == 2
    assert runtime.post_slack_thread_reply_with_ts.await_args.args[:2] == ("C1", "4.0")


async def test_history_tools_read_other_incidents_as_cited_context(incident):
    session = await runtime.load_incident_session(config())
    other = Incident(
        id="older", workspace_id="T1", channel_id="C2", channel_name="inc-db", thread_id="t2"
    )
    await service.INCIDENTS.put(other.id, other)
    from agent.incidents.models import IncidentReport

    await documents.update_from_report(other, IncidentReport(summary="Disk filled up"))

    found = await session._search_incidents("db")
    read = await session._read_incident("older")

    assert [item["id"] for item in found["items"]] == ["older"]
    assert read["evidence_id"].startswith("incident:")
    assert "Disk filled up" in read["observation"]
