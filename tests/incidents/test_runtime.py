"""Incident session loading, middleware, and the report tool on the main agent."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import langgraph_sdk
import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import HumanMessage

from agent.incidents import documents, runtime, service
from agent.incidents.models import Incident, IncidentPolicy
from agent.incidents.report import CONTEXT_MARKER
from agent.slack.channels import SlackChannel
from agent.slack.http import SlackRequestError

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
    monkeypatch.setattr(SlackChannel, "fetch", AsyncMock(return_value=dict(CHANNEL)))
    monkeypatch.setattr(runtime, "post_slack_thread_reply_with_ts", AsyncMock(return_value="7.0"))
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
    return HumanMessage(content=f"{CONTEXT_MARKER}{header}\nErrors reported")


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


async def test_failed_slack_delivery_is_retried_on_the_next_report(incident):
    session = await runtime.load_incident_session(config())
    await runtime.IncidentMiddleware(session).awrap_model_call(
        request(context_message("1.0")), AsyncMock()
    )
    draft = {"summary": [{"text": "Errors reported", "evidence_ids": ["slack:1.0"]}]}
    runtime.post_slack_thread_reply_with_ts.side_effect = SlackRequestError("channel_not_found")

    first = await session._record_incident_report(**draft)
    runtime.post_slack_thread_reply_with_ts.side_effect = None
    runtime.post_slack_thread_reply_with_ts.return_value = "8.0"
    second = await session._record_incident_report(**draft)
    third = await session._record_incident_report(**draft)

    assert (first["posted"], second["posted"], third["posted"]) == (False, True, False)
    assert runtime.post_slack_thread_reply_with_ts.await_count == 2
    latest = await service.REPORTS.get("incident")
    assert latest.posted_digest == latest.digest


async def test_postmortem_failure_records_nothing(incident, monkeypatch):
    session = await runtime.load_incident_session(config())
    monkeypatch.setattr(
        documents, "update_from_report", AsyncMock(side_effect=RuntimeError("store"))
    )
    with pytest.raises(RuntimeError, match="store"):
        await session._record_incident_report(summary=[])
    assert await service.REPORTS.get("incident") is None
    runtime.post_slack_thread_reply_with_ts.assert_not_awaited()


async def test_the_channel_gets_one_automatic_investigation_and_then_stays_quiet(
    incident, monkeypatch
):
    """Responders asked for one investigation, not a running commentary on their channel."""
    from datetime import UTC, datetime, timedelta

    session = await runtime.load_incident_session(config())
    middleware = runtime.IncidentMiddleware(session)

    await middleware.awrap_model_call(request(context_message("1.0")), AsyncMock())
    first = await session._record_incident_report(
        slack_message="**Finding**: Gateway internal errors began.",
        summary=[{"text": "Gateway internal errors began", "evidence_ids": ["slack:1.0"]}],
        problem=[{"text": "The gateway breached its 5xx SLO", "evidence_ids": ["slack:1.0"]}],
    )
    await middleware.awrap_model_call(request(context_message("2.0")), AsyncMock())
    draft = {"summary": [{"text": "Monitors are back to OK", "evidence_ids": ["slack:2.0"]}]}
    second = await session._record_incident_report(**draft)

    assert (first["posted"], second["posted"]) == (True, False)
    runtime.post_slack_thread_reply_with_ts.assert_awaited_once()
    posted = runtime.post_slack_thread_reply_with_ts.await_args.args[2]
    assert posted == "**Finding**: Gateway internal errors began."
    assert (await service.REPORTS.get("incident")).investigation_posted is True

    # A fresh conclusion much later is still not worth interrupting the channel for.
    later = (datetime.now(UTC) + timedelta(hours=9)).isoformat()
    monkeypatch.setattr(runtime, "now_iso", lambda: later)
    third = await session._record_incident_report(**draft)

    assert third["posted"] is False
    runtime.post_slack_thread_reply_with_ts.assert_awaited_once()
    # The report and the postmortem still move on without it.
    assert (await service.REPORTS.get("incident")).report.summary.startswith("Monitors are back")


async def test_an_inconclusive_first_turn_does_not_spend_the_automatic_message(incident):
    """Otherwise a first turn with nothing to say would silence the real investigation."""
    session = await runtime.load_incident_session(config())
    middleware = runtime.IncidentMiddleware(session)

    await middleware.awrap_model_call(request(context_message("1.0")), AsyncMock())
    empty = await session._record_incident_report(
        summary=[{"text": "Unsupported guess", "evidence_ids": ["nope"]}]
    )

    assert empty["posted"] is False
    runtime.post_slack_thread_reply_with_ts.assert_not_awaited()

    findings = await session._record_incident_report(
        summary=[{"text": "Deadline exhausted on oversized uploads", "evidence_ids": ["slack:1.0"]}]
    )

    assert findings["posted"] is True
    runtime.post_slack_thread_reply_with_ts.assert_awaited_once()


async def test_questions_are_answered_after_the_investigation_is_published(incident):
    """Going quiet silences unprompted updates; it never delays a responder."""
    unprompted = await runtime.load_incident_session(config())
    await runtime.IncidentMiddleware(unprompted).awrap_model_call(
        request(context_message("1.0")), AsyncMock()
    )
    await unprompted._record_incident_report(
        summary=[{"text": "Gateway internal errors began", "evidence_ids": ["slack:1.0"]}]
    )

    asked = await runtime.load_incident_session(config(incident_request="failure rate?"))
    await runtime.IncidentMiddleware(asked).awrap_model_call(
        request(context_message("2.0")), AsyncMock()
    )
    answer = await asked._record_incident_report(
        summary=[{"text": "The endpoint failure rate is 0%", "evidence_ids": ["slack:2.0"]}]
    )

    assert answer["posted"] is True
    assert runtime.post_slack_thread_reply_with_ts.await_count == 2
