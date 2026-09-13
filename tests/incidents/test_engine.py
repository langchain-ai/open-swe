"""Read boundaries and report provenance for the incident engine."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from agent.incidents import engine, evidence_tools, service
from agent.incidents.models import (
    Evidence,
    Incident,
    IncidentMessage,
    IncidentPolicy,
    IncidentReport,
)
from agent.incidents.runtime import PASSES


@pytest.fixture
def policy() -> IncidentPolicy:
    return IncidentPolicy(workspace_id="T1", enabled=True)


def test_report_keeps_supported_claims_and_drops_invented_or_partial_citations(policy):
    collected = evidence_tools.EvidenceCollector()
    collected.evidence = [Evidence(id="slack:1", source="slack", summary="Reported errors")]
    draft = engine.ReportDraft.model_validate(
        {
            "summary": [
                {"text": "Responders observed errors", "evidence_ids": ["slack:1", "slack:1"]},
                {"text": "Invented cause", "evidence_ids": ["missing"]},
                {"text": "Unsupported inference", "evidence_ids": []},
            ],
            "impact": [{"text": "Entire fleet is down", "evidence_ids": ["slack:1", "missing"]}],
            "next_steps": [
                {"text": "Consider rolling back the reported change", "evidence_ids": ["slack:1"]},
                {"text": "Disable authentication", "evidence_ids": ["missing"]},
            ],
            "hypotheses": [
                {"title": "Observed regression", "evidence_ids": ["slack:1"]},
                {"title": "Fabricated diagnosis", "evidence_ids": ["missing"]},
            ],
        }
    )

    report = engine.finalize_report(draft, collected)

    assert report.summary == "Responders observed errors [slack:1]"
    assert report.outcome == "findings"
    assert "Entire fleet" not in report.impact
    assert report.next_steps == ["Consider rolling back the reported change [slack:1]"]
    assert [hypothesis.title for hypothesis in report.hypotheses] == ["Observed regression"]
    assert report.gaps
    assert [evidence.id for evidence in report.evidence] == ["slack:1"]


@pytest.mark.parametrize("references", [[], ["invented"]])
def test_report_without_supported_summary_remains_inconclusive(policy, references):
    report = engine.finalize_report(
        engine.ReportDraft.model_validate(
            {
                "summary": [{"text": "Everything is healthy", "evidence_ids": references}],
            }
        ),
        evidence_tools.EvidenceCollector(),
    )
    assert report.outcome == "inconclusive"
    assert "Everything is healthy" not in report.summary
    assert report.gaps


def test_edited_context_replaces_citation_identity_and_deleted_messages_are_unavailable(policy):
    collected = evidence_tools.EvidenceCollector()
    context = engine.message_context(
        [
            IncidentMessage(id="1", ts="1", text="Corrected symptom", edited_at="2", user="U1"),
            IncidentMessage(id="3", ts="3", text="Deleted customer text", deleted=True),
        ],
        policy,
        collected,
    )

    assert len(context) == 1
    assert context[0]["text"] == "Corrected symptom"
    assert context[0]["evidence_id"] == "slack:1:2"
    assert context[0]["edited_at"] == "2"
    assert [evidence.id for evidence in collected.evidence] == ["slack:1:2"]
    draft = engine.ReportDraft.model_validate(
        {
            "summary": [
                {"text": "Old interpretation", "evidence_ids": ["slack:1"]},
                {"text": "Deleted interpretation", "evidence_ids": ["slack:3"]},
                {"text": "Current symptom", "evidence_ids": ["slack:1:2"]},
            ],
        }
    )
    assert engine.finalize_report(draft, collected).summary == "Current symptom [slack:1:2]"


def test_channel_membership_events_are_not_incident_evidence(policy):
    from agent.incidents import slack

    collected = evidence_tools.EvidenceCollector()
    messages = [
        slack.message("C1", {"ts": "1", "subtype": "channel_join", "text": "<@U1> joined"}),
        slack.message("C1", {"ts": "2", "subtype": "channel_leave", "text": "<@U1> left"}),
        slack.message("C1", {"ts": "3", "subtype": "bot_message", "text": "Latency alert"}),
    ]
    context = engine.message_context(messages, policy, collected)

    assert [item["text"] for item in context] == ["Latency alert"]
    assert len(collected.evidence) == 1
    assert collected.evidence[0].summary == "Message in the incident channel"


class AgentRunServer:
    """Only the remote thread/run API is simulated; incident persistence stays real."""

    def __init__(self):
        self.threads = {}
        self.runs = {}
        self.lost_create_response = False
        self.complete_report = True
        self.terminal_status = "success"
        self.waiting = asyncio.Event()
        self.block_get = False
        self.client = SimpleNamespace(
            threads=SimpleNamespace(create=self.create_thread),
            runs=SimpleNamespace(
                create=self.create_run,
                list=self.list_runs,
                get=self.get_run,
                cancel=self.cancel_run,
            ),
        )

    async def create_thread(self, *, thread_id, metadata, **kwargs):
        self.threads.setdefault(thread_id, {"thread_id": thread_id, "metadata": metadata})
        return self.threads[thread_id]

    async def create_run(self, thread_id, assistant_id, **kwargs):
        pass_id = kwargs["metadata"]["incident_pass_id"]
        saved = await PASSES.get(pass_id)
        assert saved and saved.dispatch_started
        assert saved.thread_id == thread_id
        run_id = f"run-{len(self.runs) + 1}"
        run = {
            "run_id": run_id,
            "thread_id": thread_id,
            "assistant_id": assistant_id,
            "status": "running",
            **kwargs,
        }
        self.runs[run_id] = run
        if self.lost_create_response:
            self.lost_create_response = False
            raise httpx.ReadError("Response lost after server accepted the run")
        return run

    async def list_runs(self, thread_id, **kwargs):
        return [run for run in self.runs.values() if run["thread_id"] == thread_id]

    async def get_run(self, thread_id, run_id):
        run = self.runs[run_id]
        assert run["thread_id"] == thread_id
        self.waiting.set()
        if self.block_get:
            await asyncio.Future()
        if self.complete_report:
            saved = await PASSES.get(run["metadata"]["incident_pass_id"])
            saved.report = IncidentReport(id=saved.id, summary="Observed service errors")
            await PASSES.put(saved.id, saved)
        run["status"] = self.terminal_status
        return run

    async def cancel_run(self, thread_id, run_id, *, action):
        run = self.runs[run_id]
        assert run["thread_id"] == thread_id
        assert action == "interrupt"
        run["status"] = "interrupted"


@pytest.fixture
async def dispatch_runtime(fake_store, monkeypatch, policy):
    record = Incident(
        id="incident-1",
        workspace_id="T1",
        channel_id="C1",
        title="Service errors",
        thread_id="operational-worker",
        status="investigating",
        can_read=True,
    )
    await service.INVESTIGATIONS.put(record.id, record)
    await service.POLICIES.put("default", policy)
    monkeypatch.setattr(
        service.slack,
        "channel_info",
        AsyncMock(
            return_value={
                "id": "C1",
                "name": "inc-service",
                "is_channel": True,
                "is_member": True,
                "is_private": False,
                "is_ext_shared": False,
                "is_pending_ext_shared": False,
            }
        ),
    )
    server = AgentRunServer()
    monkeypatch.setattr(service, "store_client", lambda: server.client)
    return server, record


async def test_dispatch_reuses_system_conversation_for_distinct_main_agent_passes(
    dispatch_runtime, policy
):
    server, record = dispatch_runtime
    messages = [IncidentMessage(id="1", ts="1", text="Errors began after rollout")]
    first = await engine.incidents(
        messages, policy, question="What changed?", incident_id=record.id, explicit=True
    )
    saved_record = await service.INVESTIGATIONS.get(record.id)
    first_pass_id = saved_record.active_pass_id
    saved_record.active_pass_id = None
    await service.INVESTIGATIONS.put(record.id, saved_record)

    second = await engine.incidents(messages, policy, previous_report=first, incident_id=record.id)

    assert first.id != second.id
    assert len(server.threads) == 1
    conversation = next(iter(server.threads.values()))
    assert conversation["thread_id"] != record.thread_id
    assert conversation["metadata"]["owner_type"] == "system"
    assert conversation["metadata"]["visibility"] == "public"
    assert conversation["metadata"]["incident_id"] == record.id
    assert len(server.runs) == 2
    first_run, second_run = server.runs.values()
    for run in (first_run, second_run):
        assert run["assistant_id"] == "agent"
        assert run["thread_id"] == conversation["thread_id"]
        assert run["durability"] == "sync"
        assert run["multitask_strategy"] == "reject"
        assert "github_login" not in run["config"]["configurable"]
        assert "user_email" not in run["config"]["configurable"]
        assert run["input"]["messages"][-1]["id"] == run["metadata"]["incident_pass_id"]
    assert first_run["metadata"]["incident_pass_id"] == first_pass_id
    first_input = json.loads(first_run["input"]["messages"][-1]["content"])
    assert first_input["question"] == "What changed?"
    second_input = json.loads(second_run["input"]["messages"][-1]["content"])
    assert second_input["previous_findings"]["id"] == first.id
    assert (await PASSES.get(first_pass_id)).explicit


async def test_uncertain_dispatch_recovers_matching_pass_without_creating_duplicate_run(
    dispatch_runtime, policy
):
    server, record = dispatch_runtime
    server.lost_create_response = True
    with pytest.raises(engine.IncidentExecutionError):
        await engine.incidents([], policy, incident_id=record.id)
    saved_record = await service.INVESTIGATIONS.get(record.id)
    pass_id = saved_record.active_pass_id
    pending = await PASSES.get(pass_id)
    assert pending.dispatch_started and pending.run_id is None and not pending.cancelled
    server.runs["unrelated"] = {
        "run_id": "unrelated",
        "thread_id": saved_record.agent_thread_id,
        "metadata": {"incident_pass_id": "different-pass"},
        "status": "success",
    }

    report = await engine.incidents([], policy, incident_id=record.id)

    assert report.id == pass_id
    assert (await PASSES.get(pass_id)).run_id == "run-1"
    assert len(server.runs) == 2
    assert server.runs["run-1"]["status"] == "success"


@pytest.mark.parametrize("status", ["error", "timeout", "interrupted", "success"])
async def test_terminal_main_run_without_report_cannot_be_accepted(
    dispatch_runtime, policy, status
):
    server, record = dispatch_runtime
    server.complete_report = False
    server.terminal_status = status
    with pytest.raises(engine.IncidentExecutionError, match="valid evidence report"):
        await engine.incidents([], policy, incident_id=record.id)
    saved_record = await service.INVESTIGATIONS.get(record.id)
    saved = await PASSES.get(saved_record.active_pass_id)
    assert saved.report is None
    assert saved.cancelled
    assert server.runs["run-1"]["status"] == "interrupted"


async def test_cancelled_worker_revokes_saved_pass_and_interrupts_main_run(
    dispatch_runtime, policy
):
    server, record = dispatch_runtime
    server.block_get = True
    running = asyncio.create_task(engine.incidents([], policy, incident_id=record.id))
    await asyncio.wait_for(server.waiting.wait(), timeout=1)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    saved_record = await service.INVESTIGATIONS.get(record.id)
    saved = await PASSES.get(saved_record.active_pass_id)
    assert saved.cancelled and saved.report is None
    assert server.runs["run-1"]["status"] == "interrupted"


async def test_cached_report_retry_still_checks_control_revocation(dispatch_runtime, policy):
    server, record = dispatch_runtime
    report = await engine.incidents([], policy, incident_id=record.id)

    async def revoked():
        raise PermissionError("Incident was paused")

    with pytest.raises(PermissionError, match="paused"):
        await engine.incidents([], policy, incident_id=record.id, before_tool_call=revoked)
    assert len(server.runs) == 1
    assert (await PASSES.get(report.id)).report.id == report.id


async def test_fast_main_report_is_preserved_when_dispatch_response_is_saved(
    dispatch_runtime, policy
):
    server, record = dispatch_runtime
    create_run = server.client.runs.create

    async def complete_before_response(thread_id, assistant_id, **kwargs):
        run = await create_run(thread_id, assistant_id, **kwargs)
        saved = await PASSES.get(run["metadata"]["incident_pass_id"])
        saved.evidence = [Evidence(id="fast-observation", source="mcp", summary="Observed errors")]
        saved.report = IncidentReport(id=saved.id, summary="Completed before dispatch returned")
        await PASSES.put(saved.id, saved)
        return run

    server.client.runs.create = complete_before_response
    report = await engine.incidents([], policy, incident_id=record.id)

    assert report.summary == "Completed before dispatch returned"
    saved = await PASSES.get(report.id)
    assert saved.run_id == "run-1"
    assert saved.evidence[0].id == "fast-observation"
    assert len(server.runs) == 1


async def test_engine_deadline_revokes_pass_and_interrupts_remote_run(
    dispatch_runtime, policy, monkeypatch
):
    server, record = dispatch_runtime
    server.block_get = True
    timeout = asyncio.timeout
    monkeypatch.setattr(engine.asyncio, "timeout", lambda seconds: timeout(0.01))

    with pytest.raises(engine.IncidentExecutionError) as exc:
        await engine.incidents([], policy, incident_id=record.id)

    assert isinstance(exc.value.__cause__, TimeoutError)
    saved_record = await service.INVESTIGATIONS.get(record.id)
    assert (await PASSES.get(saved_record.active_pass_id)).cancelled
    assert server.runs["run-1"]["status"] == "interrupted"


async def test_edit_reset_uses_fresh_conversation_and_drops_old_report_from_input(
    dispatch_runtime, policy
):
    server, record = dispatch_runtime
    first = await engine.incidents(
        [IncidentMessage(id="1", ts="1", text="Original claim")], policy, incident_id=record.id
    )
    saved_record = await service.INVESTIGATIONS.get(record.id)
    original_thread = saved_record.agent_thread_id
    saved_record.active_pass_id = None
    saved_record.reset_conversation = True
    old_followup = IncidentMessage(
        id="old-followup",
        ts="2",
        text="Old integration observation",
        event_type="agent_followup",
    )
    saved_record.messages.append(old_followup)
    await service.INVESTIGATIONS.put(record.id, saved_record)

    corrected = await engine.incidents(
        [IncidentMessage(id="1", ts="1", text="Corrected claim", edited_at="2"), old_followup],
        policy,
        previous_report=first,
        incident_id=record.id,
    )

    run = server.runs["run-2"]
    assert run["thread_id"] != original_thread
    content = json.loads(run["input"]["messages"][-1]["content"])
    assert content["context"][0]["text"] == "Corrected claim"
    assert len(content["context"]) == 1
    assert not (await service.INVESTIGATIONS.get(record.id)).messages
    assert content["previous_findings"] is None
    assert content["postmortem"] is None
    assert corrected.id != first.id


async def test_deleting_later_context_does_not_reopen_an_older_contaminated_checkpoint(
    dispatch_runtime, policy
):
    server, record = dispatch_runtime
    original = IncidentMessage(id="1", ts="1", text="Original symptom")
    corrected = original.model_copy(update={"text": "Corrected symptom", "edited_at": "2"})
    later = IncidentMessage(id="3", ts="3", text="Sensitive observation later removed")
    await engine.incidents([original], policy, incident_id=record.id)

    for reset, messages in [(True, [corrected]), (False, [corrected, later]), (True, [corrected])]:
        saved_record = await service.INVESTIGATIONS.get(record.id)
        saved_record.active_pass_id = None
        saved_record.reset_conversation = reset
        await service.INVESTIGATIONS.put(record.id, saved_record)
        await engine.incidents(messages, policy, incident_id=record.id)

    contaminated_thread = server.runs["run-3"]["thread_id"]
    assert contaminated_thread == server.runs["run-2"]["thread_id"]
    assert server.runs["run-4"]["thread_id"] != contaminated_thread
    latest = json.loads(server.runs["run-4"]["input"]["messages"][-1]["content"])
    assert [message["text"] for message in latest["context"]] == ["Corrected symptom"]
