from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.incidents import service, worker
from agent.incidents.models import Incident, IncidentPolicy


@pytest.fixture
async def incident(fake_store, monkeypatch):
    record = Incident(
        id="incident",
        workspace_id="T1",
        channel_id="C1",
        thread_id="worker",
        agent_thread_id="conversation",
        status="watching",
        can_read=True,
    )
    await service.INVESTIGATIONS.put(record.id, record)
    await service.POLICIES.put("default", IncidentPolicy(enabled=True, workspace_id="T1"))
    monkeypatch.setattr(service, "wake", AsyncMock())
    return record


async def test_followup_waits_until_due_and_enters_as_evidence(incident, monkeypatch):
    from agent.incidents.followups import enqueue_followup

    monkeypatch.setattr(worker.time, "time", lambda: 1000)
    result = await enqueue_followup(
        incident.agent_thread_id, "wakeup-1", "Recheck errors", delay=60
    )
    assert result["success"]
    assert await worker.channel_receipts(incident) == []
    monkeypatch.setattr(worker.time, "time", lambda: 1060)
    assert len(await worker.channel_receipts(incident)) == 1
    await worker._consume_receipts(incident, await service.get_policy(), {})
    assert "Recheck errors" in incident.messages[0].text
    assert incident.messages[0].event_type == "agent_followup"
    assert not incident.pending_requests
    assert await worker.channel_receipts(incident) == []


@pytest.mark.parametrize("change", ["generation", "policy", "scope"])
async def test_followup_cannot_cross_changed_access_scope(incident, change):
    from agent.incidents.followups import enqueue_followup

    await enqueue_followup(incident.agent_thread_id, "background-1", "Old task result")
    if change == "generation":
        incident.agent_thread_id = "new-conversation"
    elif change == "policy":
        policy = await service.get_policy()
        policy.version += 1
        await service.POLICIES.put("default", policy)
    else:
        incident.evidence_scope = "changed"
    await worker._consume_receipts(incident, await service.get_policy(), {})
    assert not incident.messages
    assert not incident.pending_requests


async def test_background_completion_queues_incident_work_instead_of_unbound_agent_run(
    incident, monkeypatch
):
    from agent import background_tasks

    backend = SimpleNamespace(aexecute=AsyncMock(return_value=SimpleNamespace(exit_code=0)))
    client = SimpleNamespace(
        threads=SimpleNamespace(
            get=AsyncMock(
                return_value={"metadata": {"source": "incidents_agent", "sandbox_id": "sandbox"}}
            )
        )
    )
    task = {"task_id": "build", "status": "completed", "exit_code": 0}
    monkeypatch.setattr(background_tasks, "_client", lambda: client)
    monkeypatch.setattr(background_tasks, "create_sandbox", AsyncMock(return_value=backend))
    monkeypatch.setattr(background_tasks, "_list_tasks", AsyncMock(return_value=[task]))
    monkeypatch.setattr(background_tasks, "_claim", AsyncMock(return_value=True))
    monkeypatch.setattr(background_tasks, "_mark_delivered", AsyncMock())
    monkeypatch.setattr(background_tasks, "_delete_crons", AsyncMock())
    dispatch = AsyncMock()
    monkeypatch.setattr(background_tasks, "dispatch_agent_run", dispatch)
    result = await background_tasks.monitor_background_tasks(incident.agent_thread_id)
    assert result["delivered"] == 1
    dispatch.assert_not_awaited()
    receipts = await worker.channel_receipts(incident)
    assert len(receipts) == 1
    assert "build" in receipts[0].payload["text"]


async def test_schedule_tool_uses_incident_worker(incident, monkeypatch):
    from agent.run_config import RunConfig
    from agent.tools.schedule_thread_wakeup import schedule_thread_wakeup

    monkeypatch.setattr(
        RunConfig,
        "from_runtime",
        lambda: RunConfig(thread_id=incident.agent_thread_id, source="incidents_agent"),
    )
    result = await schedule_thread_wakeup(5, "Check mitigation")
    assert result["success"]
    receipts = await service.RECEIPTS.search_all()
    assert len(receipts) == 1
    assert receipts[0].available_at > receipts[0].received_at


async def test_duplicate_followup_and_pending_budget(incident):
    from agent.incidents.followups import enqueue_followup

    for index in range(10):
        result = await enqueue_followup(incident.agent_thread_id, str(index), "Check", delay=60)
        assert result["success"]
    duplicate = await enqueue_followup(incident.agent_thread_id, "0", "Check", delay=60)
    assert duplicate["status"] == "duplicate"
    assert not (await enqueue_followup(incident.agent_thread_id, "11", "Check", delay=60))[
        "success"
    ]
    assert len(await service.RECEIPTS.search_all()) == 10
    incident.agent_thread_id = "new-conversation"
    await service.INVESTIGATIONS.put(incident.id, incident)
    assert (await enqueue_followup(incident.agent_thread_id, "new", "Check", delay=60))["success"]
