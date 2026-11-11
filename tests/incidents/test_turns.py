"""Dispatching one agent run per incident turn."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openswe.incidents import service, turns
from openswe.incidents.models import (
    Incident,
    IncidentPolicy,
    IncidentReport,
    IncidentReportRecord,
)
from openswe.incidents.report import CONTEXT_MARKER
from openswe.message_queue import QueuedMessage


@pytest.fixture
def policy() -> IncidentPolicy:
    return IncidentPolicy(enabled=True, workspace_id="T1", slack_app_id="A1")


@pytest.fixture
async def record(registry_db, policy):
    await service.POLICIES.put(policy)
    record = Incident(id="i1", workspace_id="T1", channel_id="C1", thread_id="thread-1")
    await service.INCIDENTS.put(record.id, record)
    return record


@pytest.fixture
def platform(monkeypatch, record):
    runs = SimpleNamespace(list=AsyncMock(return_value=[]), cancel_many=AsyncMock())
    metadata = {
        "source": "incidents_agent",
        "origin": "incidents_agent",
        "owner_type": "system",
        "visibility": "public",
        "incident_id": record.id,
    }

    async def update(thread_id, patch):
        metadata.update(patch)
        return SimpleNamespace(metadata=metadata)

    monkeypatch.setattr(
        turns.THREADS, "get", AsyncMock(return_value=SimpleNamespace(metadata=metadata))
    )
    monkeypatch.setattr(turns.THREADS, "update", AsyncMock(side_effect=update))
    client = SimpleNamespace(runs=runs, metadata=metadata)
    monkeypatch.setattr(turns, "get_client", lambda: client)
    monkeypatch.setattr(turns, "create_durable_run", AsyncMock(return_value={"run_id": "r1"}))
    monkeypatch.setattr(turns, "queue_message_for_thread", AsyncMock(return_value=True))
    monkeypatch.setattr(turns, "post_slack_thread_reply_with_ts", AsyncMock(return_value="9.0"))
    return client


def _header(payload: dict) -> dict:
    return json.loads(payload["text"].split("\n", 1)[0].removeprefix(CONTEXT_MARKER))


async def test_context_block_is_citable_attributed_and_redacted(monkeypatch):
    permalink = AsyncMock(return_value="https://example.slack.com/archives/C1/p15")
    monkeypatch.setattr(turns, "get_slack_permalink", permalink)
    human = await turns.context_block(
        "C1", {"ts": "1.5", "user": "U1", "text": "token sk-abcdefghijklmnopqrstuvwxyz0123"}
    )
    assert _header(human) == {
        "evidence_id": "slack:1.5",
        "source_url": "https://example.slack.com/archives/C1/p15",
        "author": "<@U1>",
        "ts": "1.5",
    }
    assert "[redacted]" in human["text"] and "sk-abc" not in human["text"]
    assert human["queue_id"] == "incident:C1:slack:1.5"
    assert human["sender"]["id"] == "slack:U1"

    alert = await turns.context_block(
        "C1",
        {"ts": "2.0", "bot_id": "B1", "bot_profile": {"name": "Datadog"}, "text": "p95 up"},
    )
    assert _header(alert)["author"] == "Datadog"
    assert "sender" not in alert

    from openswe.incidents import evidence_tools
    from openswe.incidents.report import context_evidence
    from openswe.slack import links

    monkeypatch.setattr(links, "dashboard_api_base_url", lambda: "https://robin.example")
    monkeypatch.setattr(evidence_tools, "dashboard_api_base_url", lambda: "https://robin.example")
    permalink.return_value = None
    edited = await turns.context_block(
        "C123",
        {"ts": "1.5", "thread_ts": "1.0", "user": "U1", "edited": {"ts": "3.0"}, "text": "x"},
    )
    assert _header(edited)["evidence_id"] == "slack:1.5:3.0"
    fallback = (
        "https://robin.example/dashboard/api/slack/open?channel=C123&message_ts=1.5&thread_ts=1.0"
    )
    assert _header(edited)["source_url"] == fallback
    collector = evidence_tools.EvidenceCollector()
    assert context_evidence(edited["text"], collector) == 1
    assert collector.evidence[0].url == fallback
    assert evidence_tools.source_url(f"{fallback}&token=secret#fragment") == fallback
    assert evidence_tools.source_url(fallback.replace("robin.example", "other.example")) == (
        "https://other.example/dashboard/api/slack/open"
    )
    assert edited["queue_id"] != human["queue_id"]


async def test_automatic_turns_go_quiet_once_the_investigation_is_published(
    record, policy, platform
):
    """The one automatic message is spent, so later turns only keep the record current."""
    await service.REPORTS.put(
        record.id,
        IncidentReportRecord(
            incident_id=record.id,
            report=IncidentReport(summary="Deadline exhausted on oversized uploads"),
            digest="d1",
            investigation_posted=True,
        ),
    )

    assert await turns.schedule_automatic_turn(record, policy) is True

    rendered = json.dumps(turns.create_durable_run.await_args.kwargs["input"])
    assert json.dumps(turns.AUTOMATIC_REQUEST)[1:-1] in rendered
    assert json.dumps(turns.FIRST_INVESTIGATION_REQUEST)[1:-1] not in rendered


async def test_explicit_turn_interrupts_and_carries_request_and_thread(record, policy, platform):
    await turns.dispatch_turn(
        record,
        policy,
        request="Why are we seeing 500s?",
        requester={"id": "slack:U1", "platform": "slack", "display_name": "SRE"},
        reply_thread_ts="4.0",
    )

    _, kwargs = turns.create_durable_run.await_args
    assert kwargs["multitask_strategy"] == "interrupt"
    assert kwargs["after_seconds"] is None
    configurable = kwargs["config"]["configurable"]
    assert configurable["incident_request"] == "Why are we seeing 500s?"
    assert configurable["github_login"] is None and configurable["user_email"] is None
    assert configurable["slack_thread"]["reply_thread_ts"] == "4.0"
    assert kwargs["metadata"]["incident_turn"] == "explicit"
    rendered = json.dumps(kwargs["input"])
    assert "Why are we seeing 500s?" in rendered and "slack:U1" in rendered


async def test_github_followup_corruption_is_repaired_only_for_bound_system_incident(
    record, policy, platform, monkeypatch
):
    from openswe.incidents import runtime

    platform.metadata.update(source="github", origin="github")
    await turns.dispatch_turn(record, policy, request="What changed?")
    kwargs = turns.create_durable_run.await_args.kwargs
    monkeypatch.setattr(
        runtime.langgraph_sdk,
        "get_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(get=AsyncMock(return_value={"metadata": platform.metadata}))
        ),
    )
    inherited = {"github_login": "pr-commenter", "user_email": "human@example.com"}
    inherited.update(kwargs["config"]["configurable"])
    session = await runtime.load_incident_session({"configurable": inherited}, run_id="after-pr")
    assert session.explicit_request == "What changed?"
    assert platform.metadata["source"] == platform.metadata["origin"] == "incidents_agent"
    with pytest.raises(PermissionError, match="personal"):
        await runtime.load_incident_session(
            {"configurable": {**inherited, "github_login": "forged"}}, run_id="forged"
        )


@pytest.mark.parametrize(
    "change",
    [{"owner_type": "user"}, {"visibility": "private"}, {"incident_id": "other"}],
)
async def test_dispatch_binding_failures_are_visible_without_promoting_threads(
    record, policy, platform, change
):
    platform.metadata.update(source="github", **change)
    with pytest.raises(PermissionError, match="binding"):
        await turns.dispatch_turn(record, policy, request="Investigate", reply_thread_ts="4.0")
    turns.create_durable_run.assert_not_awaited()
    assert platform.metadata["source"] == "github"
    notice = turns.post_slack_thread_reply_with_ts.await_args.args
    assert notice[:2] == ("C1", "4.0")
    assert "couldn't start" in notice[2] and "binding" not in notice[2]
    turns.post_slack_thread_reply_with_ts.assert_awaited_once()


async def test_explicit_startup_failure_posts_once_automatic_failure_stays_quiet(
    record, policy, platform
):
    turns.create_durable_run.side_effect = RuntimeError("sensitive internal detail")
    with pytest.raises(RuntimeError):
        await turns.dispatch_turn(record, policy, request="Investigate")
    turns.post_slack_thread_reply_with_ts.assert_awaited_once()
    assert "sensitive" not in turns.post_slack_thread_reply_with_ts.await_args.args[2]
    with pytest.raises(RuntimeError):
        await turns.dispatch_turn(record, policy)
    turns.post_slack_thread_reply_with_ts.assert_awaited_once()


async def test_completion_reschedules_only_stranded_context(record, policy, platform):
    assert (await turns.handle_run_completion("thread-1", "r1", "success"))["reason"] == (
        "incident turn complete"
    )
    turns.create_durable_run.assert_not_awaited()

    await QueuedMessage.put("thread-1", "late")
    result = await turns.handle_run_completion("thread-1", "r1", "success")
    assert result["reason"] == "queued incident context rescheduled"
    turns.create_durable_run.assert_awaited_once()

    record.status = "paused"
    await service.INCIDENTS.put(record.id, record)
    await turns.handle_run_completion("thread-1", "r2", "success")
    turns.create_durable_run.assert_awaited_once()
    assert (await turns.handle_run_completion("unknown", "r3", "success"))["status"] == "ignored"


async def test_completion_failure_flags_attention_and_posts_once(record, policy, platform):
    first = await turns.handle_run_completion("thread-1", "r1", "error")
    again = await turns.handle_run_completion("thread-1", "r1", "error")

    saved = await service.INCIDENTS.get("i1")
    assert first["status"] == "ok" and again["status"] == "ignored"
    assert (saved.status, saved.reason) == ("needs_attention", "run_failed")
    assert saved.activity[-1].type == "error"
    turns.post_slack_thread_reply_with_ts.assert_awaited_once()
    assert turns.post_slack_thread_reply_with_ts.await_args.args[:2] == ("C1", "0")
    assert (await turns.handle_run_completion("thread-1", "r9", "interrupted"))["status"] == (
        "ignored"
    )
