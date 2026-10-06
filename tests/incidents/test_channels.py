"""Slack events for enrolled incident channels."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks, HTTPException

from agent.incidents import channels, service, turns
from agent.incidents.models import Incident, IncidentPolicy
from agent.slack.channels import SlackChannel

CHANNEL = {
    "id": "C1",
    "name": "inc-api",
    "is_channel": True,
    "is_private": False,
    "is_im": False,
    "is_mpim": False,
    "is_ext_shared": False,
    "is_pending_ext_shared": False,
    "is_member": True,
}


@pytest.fixture
async def configured(fake_store, monkeypatch):
    await service.POLICIES.put(
        "default", IncidentPolicy(enabled=True, workspace_id="T1", slack_app_id="A1")
    )
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(channels.User, "login_for_slack", AsyncMock(return_value="sre"))
    monkeypatch.setattr(channels, "post_account_link_prompt", AsyncMock())
    joined = AsyncMock()

    @asynccontextmanager
    async def slack():
        yield SimpleNamespace(conversations_join=joined)

    monkeypatch.setattr(channels.SlackClient, "bot", slack)
    monkeypatch.setattr(channels, "claim_slack_event", AsyncMock(return_value=True))
    monkeypatch.setattr(
        channels,
        "dashboard_incident_url",
        lambda incident_id: f"https://dash/incidents/{incident_id}",
    )
    monkeypatch.setattr(SlackChannel, "fetch", AsyncMock(return_value=dict(CHANNEL)))
    monkeypatch.setattr(
        channels,
        "get_slack_user_info",
        AsyncMock(return_value={"profile": {"email": "sre@example.com", "display_name": "SRE"}}),
    )
    monkeypatch.setattr(channels, "resolve_slack_thread_id", AsyncMock(return_value="thread-1"))
    monkeypatch.setattr(channels, "upsert_agent_thread_metadata", AsyncMock(return_value=True))
    monkeypatch.setattr(channels, "post_slack_thread_reply_with_ts", AsyncMock(return_value="5.0"))
    monkeypatch.setattr(
        channels,
        "fetch_slack_thread_messages",
        AsyncMock(
            return_value=[
                {"ts": "1.0", "user": "U1", "text": "Errors are up"},
                {"ts": "1.1", "user": "UBOT", "text": "own post"},
                {"ts": "1.2", "user": "U2", "subtype": "channel_join", "text": "joined"},
                {"ts": "1.3", "bot_id": "B1", "bot_profile": {"name": "Datadog"}, "text": "alert"},
            ]
        ),
    )
    threads = SimpleNamespace(update=AsyncMock())
    monkeypatch.setattr(channels, "store_client", lambda: SimpleNamespace(threads=threads))
    monkeypatch.setattr(turns, "queue_context", AsyncMock(return_value=True))
    monkeypatch.setattr(turns, "schedule_automatic_turn", AsyncMock(return_value=True))
    monkeypatch.setattr(turns, "dispatch_turn", AsyncMock(return_value={"run_id": "r1"}))
    monkeypatch.setattr(turns, "cancel_active_runs", AsyncMock())
    monkeypatch.setattr(turns, "queued_context_count", AsyncMock(return_value=0))
    return SimpleNamespace(joined=joined, threads=threads)


@pytest.fixture
async def enrolled(configured):
    record = Incident(
        id=service.incident_id("T1", "C1"),
        workspace_id="T1",
        channel_id="C1",
        channel_name="inc-api",
        thread_id="thread-1",
        anchor_ts="5.0",
    )
    await service.INCIDENTS.put(record.id, record)
    return record


def payload(event: dict, *, team_id="T1", api_app_id="A1", event_id="E1") -> dict:
    return {"team_id": team_id, "api_app_id": api_app_id, "event_id": event_id, "event": event}


async def handle(event: dict, **overrides) -> tuple[dict | None, BackgroundTasks]:
    tasks = BackgroundTasks()
    response = await channels.handle_slack_event(payload(event, **overrides), tasks)
    for task in tasks.tasks:
        await task()
    return response, tasks


async def test_enrollment_binds_thread_queues_history_and_starts_one_turn(configured):
    response, _ = await handle(
        {"type": "channel_created", "channel": {"id": "C1", "name": "inc-api"}}
    )
    assert response == {"status": "accepted"}

    record = await service.INCIDENTS.get(service.incident_id("T1", "C1"))
    assert record is not None
    assert (record.thread_id, record.anchor_ts, record.status) == ("thread-1", "5.0", "watching")
    configured.joined.assert_awaited_once_with(channel="C1")
    upsert = channels.upsert_agent_thread_metadata.await_args
    assert upsert.args == ("thread-1",)
    assert upsert.kwargs["source"] == "incidents_agent"
    assert upsert.kwargs["owner_type"] == "system" and upsert.kwargs["visibility"] == "public"
    assert upsert.kwargs["source_context"].slack_thread.thread_ts == "0"
    configured.threads.update.assert_awaited_once_with(
        thread_id="thread-1", metadata={"incident_id": record.id}
    )
    queued = [call.args[1]["ts"] for call in turns.queue_context.await_args_list]
    assert queued == ["1.0", "1.3"]
    turns.schedule_automatic_turn.assert_awaited_once()
    assert "Open incident" in channels.post_slack_thread_reply_with_ts.await_args.args[2]

    again, _ = await handle({"type": "channel_rename", "channel": {"id": "C1", "name": "inc-api"}})
    assert again == {"status": "ignored"}
    configured.joined.assert_awaited_once()


@pytest.mark.parametrize("field", ["team_id", "api_app_id"])
async def test_wrong_installation_is_rejected(configured, field):
    with pytest.raises(HTTPException) as error:
        await handle(
            {"type": "channel_created", "channel": {"id": "C1", "name": "inc-api"}},
            **{field: "wrong"},
        )
    assert error.value.status_code == 401


async def test_duplicate_deliveries_are_dropped(enrolled):
    channels.claim_slack_event.return_value = False
    response, _ = await handle({"type": "message", "channel": "C1", "text": "again"})
    assert response == {"status": "duplicate"}
    turns.queue_context.assert_not_awaited()


async def test_paused_channels_keep_context_without_scheduling(enrolled):
    enrolled.status = "paused"
    await service.INCIDENTS.put(enrolled.id, enrolled)

    response, _ = await handle(
        {"type": "message", "channel": "C1", "user": "U1", "text": "more", "ts": "3.0"}
    )

    assert response == {"status": "accepted"}
    turns.queue_context.assert_awaited_once()
    turns.schedule_automatic_turn.assert_not_awaited()


async def test_anyone_in_the_channel_can_pause_but_questions_need_a_connected_account(enrolled):
    channels.User.login_for_slack.return_value = None
    paused, _ = await handle(
        {"type": "app_mention", "channel": "C1", "user": "U9", "text": "<@UBOT> pause", "ts": "3.0"}
    )
    asked, _ = await handle(
        {"type": "app_mention", "channel": "C1", "user": "U9", "text": "<@UBOT> why?", "ts": "3.1"},
        event_id="E2",
    )

    assert paused == asked == {"status": "accepted"}
    assert (await service.INCIDENTS.get(enrolled.id)).status == "paused"
    turns.dispatch_turn.assert_not_awaited()
    channels.post_account_link_prompt.assert_awaited_once()
    assert channels.post_account_link_prompt.await_args.args[:3] == ("C1", "3.1", "U9")


async def test_pause_cancels_runs_and_tells_the_channel(enrolled):
    response, _ = await handle(
        {"type": "app_mention", "channel": "C1", "user": "U1", "text": "<@UBOT> pause", "ts": "6.0"}
    )
    assert response == {"status": "accepted"}
    turns.cancel_active_runs.assert_awaited_once_with("thread-1", keep_run_id="")
    saved = await service.INCIDENTS.get(enrolled.id)
    assert (saved.status, saved.reason) == ("paused", "responder_pause")
    assert saved.activity[-1].type == "control"
    assert "paused" in channels.post_slack_thread_reply_with_ts.await_args.args[2]


async def test_resume_reschedules_when_context_is_waiting(enrolled):
    enrolled.status = "paused"
    turns.queued_context_count.return_value = 2

    await channels.apply_control(enrolled, "resume", {"id": "github:sre"})

    assert (await service.INCIDENTS.get(enrolled.id)).status == "watching"
    turns.schedule_automatic_turn.assert_awaited_once()


async def test_excluded_channels_are_ignored_after_enrollment(enrolled):
    await service.POLICIES.put(
        "default",
        IncidentPolicy(
            enabled=True, workspace_id="T1", slack_app_id="A1", excluded_channel_ids=["C1"]
        ),
    )
    response, _ = await handle(
        {"type": "message", "channel": "C1", "user": "U1", "text": "x", "ts": "9.0"}
    )
    mention, _ = await handle(
        {
            "type": "app_mention",
            "channel": "C1",
            "user": "U1",
            "text": "<@UBOT> status?",
            "ts": "9.1",
        },
        event_id="E2",
    )
    assert response == mention == {"status": "ignored"}
    turns.queue_context.assert_not_awaited()
    turns.dispatch_turn.assert_not_awaited()


async def test_complete_records_the_run_that_completed_it(enrolled):
    """Reopening is only refused for the run that completed the incident, so record it."""
    await channels.apply_control(enrolled, "complete", {"id": "agent:incidents"}, keep_run_id="r9")
    completed = await service.INCIDENTS.get(enrolled.id)
    assert completed.completed_run_id == "r9"

    await channels.apply_control(completed, "reopen", {"id": "slack:U1"})
    assert (await service.INCIDENTS.get(enrolled.id)).completed_run_id == ""
