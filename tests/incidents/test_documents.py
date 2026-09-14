from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from agent.incidents import documents, service
from agent.incidents.models import Evidence, Hypothesis, Incident, IncidentPolicy, IncidentReport

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
async def record(fake_store, monkeypatch):
    await service.POLICIES.put(
        "default", IncidentPolicy(enabled=True, workspace_id="T1", slack_app_id="A1")
    )
    monkeypatch.setattr(service, "get_slack_channel_info", AsyncMock(return_value=dict(CHANNEL)))
    incident = Incident(
        id="incident-1",
        workspace_id="T1",
        channel_id="C1",
        channel_name="inc-api",
        title="API availability",
        thread_id="thread-1",
    )
    await service.INCIDENTS.put(incident.id, incident)
    return incident


def report(summary="Error rate increased"):
    return IncidentReport(
        summary=summary,
        impact="API requests failed",
        next_steps=["Consider reverting the deploy [slack-1]"],
        hypotheses=[Hypothesis(title="Cache regression", assessment="rejected")],
        evidence=[
            Evidence(
                id="slack-1",
                source="slack",
                url="https://slack.com/archives/C1/p1",
                summary="Raw sensitive evidence excerpt",
            )
        ],
        gaps=["Cause is unknown"],
    )


async def current(record):
    return (await documents.document_context(record.id))["postmortem"]


async def test_reports_replace_one_summary_and_keep_other_incidents(record):
    await documents.update_from_report(record, report())
    first = await current(record)
    second = record.model_copy(update={"id": "incident-2", "channel_id": "C2"})
    await service.INCIDENTS.put(second.id, second)
    await documents.update_from_report(second, report("Other incident"))
    await documents.update_from_report(record, report("Recovered"))
    await documents.update_from_report(record, report("Recovered"))
    latest = await current(record)
    assert "Error rate increased" in first["markdown"]
    assert "Error rate increased" not in latest["markdown"]
    assert latest["markdown"].count("Recovered") == 1
    assert "Other incident" in (await current(second))["markdown"]
    from agent.store import search_all_values

    assert len(await search_all_values(documents.SUMMARIES)) == 2
    assert "Raw sensitive evidence excerpt" not in latest["markdown"]
    assert "[1]: <https://slack.com/archives/C1/p1>" in latest["markdown"]


async def test_curated_history_keeps_titles_but_follows_channel_access(record):
    await documents.update_from_report(record, report())
    record.title, record.channel_name = "", ""
    await service.INCIDENTS.put(record.id, record)
    assert (await documents.search_history(q="availability"))["items"][0]["title"] == (
        "API availability"
    )
    service.get_slack_channel_info.return_value = {**CHANNEL, "is_member": False}
    assert (await documents.search_history())["items"] == []
    with pytest.raises(HTTPException) as error:
        await current(record)
    assert error.value.status_code == 404


@pytest.mark.parametrize(("info", "status"), [(None, 503), ({"is_member": False}, 404)])
async def test_document_reads_distinguish_outages_from_revocation(record, info, status):
    service.get_slack_channel_info.return_value = info
    with pytest.raises(HTTPException) as error:
        await current(record)
    assert error.value.status_code == status


async def test_store_outage_is_not_an_empty_summary(record, monkeypatch):
    monkeypatch.setattr(documents, "get_value", AsyncMock(side_effect=RuntimeError("offline")))
    with pytest.raises(RuntimeError, match="offline"):
        await current(record)


async def test_documents_api_is_read_only_and_checks_channel_access(record):
    from agent.incidents import api as incidents_api
    from agent.incidents.document_api import router

    await documents.update_from_report(record, report())
    app = FastAPI()
    app.include_router(router, prefix="/documents")
    app.dependency_overrides[incidents_api.require_session] = lambda: {"sub": "test-user"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get(f"/documents/{record.id}")).json()["postmortem"]["markdown"]
        assert (await client.put(f"/documents/{record.id}/postmortem", json={})).status_code == 404
        service.get_slack_channel_info.return_value = {**CHANNEL, "is_member": False}
        assert (await client.get(f"/documents/{record.id}")).status_code == 404


async def test_history_cannot_follow_rebound_incident_into_a_different_workspace(record):
    await documents.update_from_report(record, report())
    record.workspace_id = "T2"
    await service.INCIDENTS.put(record.id, record)
    await service.POLICIES.put("default", IncidentPolicy(workspace_id="T2"))
    with pytest.raises(HTTPException) as error:
        await current(record)
    assert error.value.status_code == 404


async def test_history_pages_use_stable_cursors(record):
    await documents.update_from_report(record, report())
    second = record.model_copy(update={"id": "incident-2", "channel_id": "C2", "title": "Other"})
    await service.INCIDENTS.put(second.id, second)
    await documents.update_from_report(second, report("Other incident"))

    page_one = await documents.search_history(limit=1)
    page_two = await documents.search_history(limit=1, cursor=page_one["next_cursor"])

    assert "|" in page_one["next_cursor"]
    assert {page_one["items"][0]["id"], page_two["items"][0]["id"]} == {record.id, second.id}
    assert page_two["next_cursor"] is None
