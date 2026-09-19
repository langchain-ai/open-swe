"""Dispatching one agent run per incident turn."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from agent.incidents import service, turns
from agent.incidents.models import Incident, IncidentPolicy
from agent.incidents.report import CONTEXT_MARKER


@pytest.fixture
def policy() -> IncidentPolicy:
    return IncidentPolicy(enabled=True, workspace_id="T1", slack_app_id="A1")


@pytest.fixture
async def record(fake_store, policy):
    await service.POLICIES.put("default", policy)
    record = Incident(id="i1", workspace_id="T1", channel_id="C1", thread_id="thread-1")
    await service.INCIDENTS.put(record.id, record)
    return record


@pytest.fixture
def platform(fake_store, monkeypatch):
    runs = SimpleNamespace(list=AsyncMock(return_value=[]), cancel_many=AsyncMock())
    client = SimpleNamespace(runs=runs, store=fake_store)
    monkeypatch.setattr(turns, "store_client", lambda: client)
    monkeypatch.setattr(turns, "create_durable_run", AsyncMock(return_value={"run_id": "r1"}))
    monkeypatch.setattr(turns, "queue_message_for_thread", AsyncMock(return_value=True))
    monkeypatch.setattr(
        turns, "post_slack_thread_reply_with_ts", AsyncMock(return_value=("9.0", None))
    )
    return client


def _header(payload: dict) -> dict:
    return json.loads(payload["text"].split("\n", 1)[0].removeprefix(CONTEXT_MARKER))


def test_context_block_is_citable_attributed_and_redacted():
    human = turns.context_block(
        "C1", {"ts": "1.5", "user": "U1", "text": "token sk-abcdefghijklmnopqrstuvwxyz0123"}
    )
    assert _header(human) == {
        "evidence_id": "slack:1.5",
        "source_url": "https://slack.com/archives/C1/p15",
        "author": "<@U1>",
        "ts": "1.5",
    }
    assert "[redacted]" in human["text"] and "sk-abc" not in human["text"]
    assert human["queue_id"] == "incident:C1:slack:1.5"
    assert human["sender"]["id"] == "slack:U1"

    alert = turns.context_block(
        "C1",
        {"ts": "2.0", "bot_id": "B1", "bot_profile": {"name": "Datadog"}, "text": "p95 up"},
    )
    assert _header(alert)["author"] == "Datadog"
    assert "sender" not in alert

    edited = turns.context_block(
        "C1", {"ts": "1.5", "user": "U1", "edited": {"ts": "3.0"}, "text": "x"}
    )
    assert _header(edited)["evidence_id"] == "slack:1.5:3.0"
    assert edited["queue_id"] != human["queue_id"]


@pytest.mark.parametrize("status", ["pending", "running"])
async def test_schedule_skips_when_a_run_is_already_inflight(record, policy, platform, status):
    platform.runs.list.side_effect = lambda thread_id, status=None: (
        [{"run_id": "r0"}] if status == status_ else []
    )
    status_ = status

    assert await turns.schedule_automatic_turn(record, policy) is False
    turns.create_durable_run.assert_not_awaited()


async def test_schedule_creates_one_debounced_system_turn(record, policy, platform):
    assert await turns.schedule_automatic_turn(record, policy) is True

    turns.create_durable_run.assert_awaited_once()
    args, kwargs = turns.create_durable_run.await_args
    assert args == ("thread-1", "agent")
    assert kwargs["after_seconds"] == turns.AUTOMATIC_DELAY_SECONDS
    assert kwargs["multitask_strategy"] == "reject"
    configurable = kwargs["config"]["configurable"]
    assert configurable["source"] == "incidents_agent"
    assert configurable["incident_id"] == "i1"
    assert configurable["slack_thread"] == {"channel_id": "C1", "thread_ts": "0"}
    assert not {"github_login", "user_email"} & configurable.keys()
    # Present and null, so a stale question on the thread cannot survive into this turn.
    assert configurable["incident_request"] is None
    assert kwargs["metadata"]["incident_turn"] == "automatic"
    assert "New activity" in json.dumps(kwargs["input"])


async def test_paused_and_completed_incidents_do_not_schedule(record, policy, platform):
    for status in ("paused", "completed"):
        record.status = status
        assert await turns.schedule_automatic_turn(record, policy) is False
    turns.create_durable_run.assert_not_awaited()


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
    assert configurable["slack_thread"]["reply_thread_ts"] == "4.0"
    assert kwargs["metadata"]["incident_turn"] == "explicit"
    rendered = json.dumps(kwargs["input"])
    assert "Why are we seeing 500s?" in rendered and "slack:U1" in rendered


async def test_completion_reschedules_only_stranded_context(record, policy, platform, fake_store):
    assert (await turns.handle_run_completion("thread-1", "r1", "success"))["reason"] == (
        "incident turn complete"
    )
    turns.create_durable_run.assert_not_awaited()

    fake_store.seed(("queue", "thread-1"), "pending_messages", {"messages": [{"content": "late"}]})
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


async def test_cancel_interrupts_pending_and_running_runs(record, platform):
    platform.runs.list.side_effect = lambda thread_id, status=None: {
        "pending": [{"run_id": "a"}],
        "running": [{"run_id": "b"}],
    }.get(status, [])

    await turns.cancel_active_runs("thread-1")

    platform.runs.cancel_many.assert_awaited_once_with(
        thread_id="thread-1", run_ids=["a", "b"], action="interrupt"
    )
    platform.runs.cancel_many.reset_mock()
    await turns.cancel_active_runs("thread-1", keep_run_id="b")
    platform.runs.cancel_many.assert_awaited_once_with(
        thread_id="thread-1", run_ids=["a"], action="interrupt"
    )
    assert await turns.has_active_run("thread-1") is True
    # The stubbed `runs.list` always reports "a" as pending (it doesn't model
    # cancellation taking effect), and queued_context_count now counts real
    # pending runs alongside the legacy KV queue (empty here) — so 1, not 0.
    assert await turns.queued_context_count("thread-1") == 1


async def test_platform_rejection_means_a_turn_is_already_scheduled(record, policy, platform):
    request = httpx.Request("POST", "http://localhost:2024/runs")
    turns.create_durable_run.side_effect = httpx.HTTPStatusError(
        "conflict", request=request, response=httpx.Response(409, request=request)
    )
    assert await turns.schedule_automatic_turn(record, policy) is False

    turns.create_durable_run.side_effect = httpx.HTTPStatusError(
        "boom", request=request, response=httpx.Response(500, request=request)
    )
    with pytest.raises(httpx.HTTPStatusError):
        await turns.schedule_automatic_turn(record, policy)
