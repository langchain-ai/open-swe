from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from agent.incidents import service
from agent.incidents.models import (
    Evidence,
    Hypothesis,
    Incident,
    IncidentMessage,
    IncidentPolicy,
    IncidentReport,
)


@pytest.fixture
async def record(fake_store, monkeypatch):
    await service.POLICIES.put(
        "default", IncidentPolicy(enabled=True, workspace_id="T1", slack_app_id="A1")
    )
    monkeypatch.setattr(service, "_wake", AsyncMock())
    monkeypatch.setattr(
        service.slack,
        "channel_info",
        AsyncMock(
            return_value={
                "id": "C1",
                "name": "inc-api",
                "is_channel": True,
                "is_private": False,
                "is_ext_shared": False,
                "is_pending_ext_shared": False,
                "is_member": True,
            }
        ),
    )
    incident = Incident(
        id="incident-1",
        workspace_id="T1",
        channel_id="C1",
        channel_name="inc-api",
        title="API availability",
        thread_id="worker-1",
        status="watching",
        can_read=True,
        messages=[IncidentMessage(id="1", ts="1", source_url="https://slack.com/archives/C1/p1")],
    )
    await service.INVESTIGATIONS.put(incident.id, incident)
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


async def edit(record, markdown, revision, request_id="edit-1", kind="postmortem"):
    from agent.incidents import documents

    return await documents.submit_edit(
        record.id,
        kind=kind,
        markdown=markdown,
        expected_revision=revision,
        request_id=request_id,
        actor={"id": "github:responder"},
    )


async def apply(record, operation):
    from agent.incidents import documents

    receipt = await service.RECEIPTS.get(operation["id"])
    assert receipt is not None
    assert await documents.process_document_receipt(record, receipt)
    return await documents.get_operation(record.id, operation["id"])


async def test_human_edit_is_queued_then_conflicting_agent_update_preserves_it(record):
    from agent.incidents import documents

    await documents.update_from_report(record, report(), expected_revision=0, run_id="run-1")
    first = (await documents.document_context(record.id))["postmortem"]
    operation = await edit(record, "# Human assessment\n\nDatabase saturation confirmed.", 1)
    assert operation["status"] == "pending"
    assert (await documents.document_context(record.id))["postmortem"] == first
    assert (await apply(record, operation))["status"] == "applied"
    await documents.update_from_report(
        record, report("Stale hypothesis"), expected_revision=1, run_id="run-2"
    )
    context = await documents.document_context(record.id)
    assert context["postmortem"]["revision"] == 2
    assert (
        context["postmortem"]["markdown"] == "# Human assessment\n\nDatabase saturation confirmed."
    )
    assert any(op["status"] == "conflict" for op in context["operations"])


async def test_successive_reports_retain_human_sections_and_deduplicate_retries(record):
    from agent.incidents import documents

    await documents.update_from_report(record, report(), expected_revision=0, run_id="run-1")
    await apply(record, await edit(record, "# Postmortem\n\n## Follow-ups\n\nHuman-owned task", 1))
    await documents.update_from_report(
        record, report("Roll back reduced errors"), expected_revision=2, run_id="run-2"
    )
    await documents.update_from_report(
        record, report("Roll back reduced errors"), expected_revision=2, run_id="run-2"
    )
    current = (await documents.document_context(record.id))["postmortem"]
    assert current["revision"] == 3
    assert "Human-owned task" in current["markdown"]
    assert "Roll back reduced errors" in current["markdown"]
    assert "Suggested next steps:\n- Consider reverting the deploy [slack-1]" in current["markdown"]
    assert "\n\nWorking hypotheses:\n- Cache regression (rejected)" in current["markdown"]
    assert "Raw sensitive evidence excerpt" not in current["markdown"]
    revisions = (await documents.list_revisions(record.id, "postmortem"))["items"]
    assert len(revisions) == 3
    assert (await documents.get_revision(record.id, "postmortem", 1))["markdown"] != current[
        "markdown"
    ]
    assert current["author"] == "agent"
    assert current["run_id"] == "run-2"
    assert current["expected_revision"] == 2


async def test_status_draft_is_separate_and_operation_identity_survives_receipt_cleanup(record):
    from agent.incidents import documents

    await documents.update_from_report(record, report(), expected_revision=0, run_id="run-1")
    operation = await edit(record, "We are investigating API errors.", 0, kind="status_page_draft")
    assert (await apply(record, operation))["revision"] == 1
    await service.RECEIPTS.delete(operation["id"])
    retry = await edit(record, "We are investigating API errors.", 0, kind="status_page_draft")
    assert retry["status"] == "applied"
    with pytest.raises(HTTPException) as exc:
        await edit(record, "Different content", 0, kind="status_page_draft")
    assert exc.value.status_code == 409
    assert (await documents.document_context(record.id))["postmortem"]["revision"] == 1
    stale = await edit(record, "Outdated draft", 0, "edit-2", "status_page_draft")
    assert (await apply(record, stale))["status"] == "conflict"
    assert len((await documents.list_revisions(record.id, "status_page_draft"))["items"]) == 1


async def test_retry_recovers_revision_after_crash_before_operation_result(record, monkeypatch):
    from agent.incidents import documents

    operation = await edit(record, "Durable text", 0)
    put = documents.OPERATIONS.put
    monkeypatch.setattr(
        documents.OPERATIONS, "put", AsyncMock(side_effect=RuntimeError("store unavailable"))
    )
    with pytest.raises(RuntimeError, match="store unavailable"):
        await apply(record, operation)
    monkeypatch.setattr(documents.OPERATIONS, "put", put)
    assert (await apply(record, operation))["status"] == "applied"
    assert len((await documents.list_revisions(record.id, "postmortem"))["items"]) == 1


async def test_curated_history_survives_expiry_but_evidence_and_revoked_channel_do_not(
    record, monkeypatch
):
    from agent.incidents import documents

    await documents.update_from_report(record, report(), expected_revision=0, run_id="run-1")
    assert (await documents.document_context(record.id))["postmortem"]["evidence"][0]["available"]
    record.expired = True
    record.messages = []
    record.report = None
    record.title = ""
    record.channel_name = ""
    await service.INVESTIGATIONS.put(record.id, record)
    context = await documents.document_context(record.id)
    assert "Error rate increased" in context["postmortem"]["markdown"]
    assert context["postmortem"]["evidence"][0]["available"] is False
    assert context["postmortem"]["evidence"][0]["url"] == ""
    assert (await documents.search_history(q="availability"))["items"][0][
        "title"
    ] == "API availability"
    monkeypatch.setattr(service.slack, "channel_info", AsyncMock(return_value={"is_private": True}))
    assert (await documents.search_history())["items"] == []
    with pytest.raises(HTTPException) as exc:
        await documents.get_revision(record.id, "postmortem", 1)
    assert exc.value.status_code == 404


async def test_worker_rechecks_scope_and_rejects_queued_edit_after_revocation(record, monkeypatch):
    from agent.incidents import documents

    operation = await edit(record, "Cannot apply after revocation", 0)
    receipt = await service.RECEIPTS.get(operation["id"])
    monkeypatch.setattr(service.slack, "channel_info", AsyncMock(return_value={"is_member": False}))
    assert await documents.process_document_receipt(record, receipt)
    assert await documents.REVISIONS.search_all() == []
    assert (await documents.OPERATIONS.get(operation["id"])).status == "rejected"


@pytest.mark.parametrize(
    "code,status", [("rate_limited", 503), ("channel_not_found", 404), ("token_revoked", 404)]
)
async def test_document_and_detail_reads_distinguish_outages_from_revocation(
    record, monkeypatch, code, status
):
    from agent.incidents import documents

    monkeypatch.setattr(
        service.slack, "channel_info", AsyncMock(side_effect=service.slack.SlackError(code))
    )
    for read in (documents.document_context, service.get_incident):
        with pytest.raises(HTTPException) as error:
            await read(record.id)
        assert error.value.status_code == status


async def test_temporary_slack_failure_keeps_document_edit_queued(record, monkeypatch):
    from agent.incidents import documents

    operation = await edit(record, "Responder mitigation notes", 0)
    channel_info = service.slack.channel_info
    monkeypatch.setattr(
        service.slack, "channel_info", AsyncMock(side_effect=httpx.ConnectTimeout("offline"))
    )
    with pytest.raises(HTTPException) as error:
        await apply(record, operation)
    assert error.value.status_code == 503
    assert await documents.REVISIONS.search_all() == []
    assert await documents.OPERATIONS.get(operation["id"]) is None
    monkeypatch.setattr(service.slack, "channel_info", channel_info)
    assert (await apply(record, operation))["status"] == "applied"


async def test_documents_api_requires_responder_and_rechecks_channel_scope(record, monkeypatch):
    from agent.dashboard import incidents_api
    from agent.incidents.document_api import router

    app = FastAPI()
    app.include_router(router, prefix="/dashboard/api/incidents/documents")
    app.dependency_overrides[incidents_api.require_session] = lambda: {"sub": "test-user"}
    monkeypatch.setattr(incidents_api, "is_observability_authorized", lambda *args, **kwargs: False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (
            await client.get(f"/dashboard/api/incidents/documents/{record.id}")
        ).status_code == 403
        monkeypatch.setattr(
            incidents_api, "is_observability_authorized", lambda *args, **kwargs: True
        )
        response = await client.put(
            f"/dashboard/api/incidents/documents/{record.id}/postmortem",
            json={
                "markdown": "Responder edit",
                "expected_revision": 0,
                "request_id": "http-edit",
            },
        )
        assert response.status_code == 202
        assert response.json()["status"] == "pending"
        assert (await client.get(f"/dashboard/api/incidents/documents/{record.id}")).json()[
            "postmortem"
        ] is None
        monkeypatch.setattr(
            service.slack, "channel_info", AsyncMock(return_value={"is_member": False})
        )
        assert (
            await client.get(f"/dashboard/api/incidents/documents/{record.id}")
        ).status_code == 404


async def test_history_cannot_follow_rebound_incident_into_a_different_workspace(record):
    from agent.incidents import documents

    await documents.update_from_report(record, report(), expected_revision=0, run_id="run-1")
    record.workspace_id = "T2"
    await service.INVESTIGATIONS.put(record.id, record)
    await service.POLICIES.put("default", IncidentPolicy(workspace_id="T2"))
    with pytest.raises(HTTPException) as exc:
        await documents.document_context(record.id)
    assert exc.value.status_code == 404
    assert (await documents.search_history())["items"] == []
