"""Joining new channels a channel name rule matches and starting its kickoff run."""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openswe.jobs.queue import Job
from openswe.routing import channel_rules as channels
from openswe.routing.store import ChannelRuleSettings
from openswe.slack.channels import SlackChannel

CHANNEL = {
    "id": "C1",
    "name": "inc-api",
    "is_channel": True,
    "is_private": False,
    "is_im": False,
    "is_mpim": False,
    "is_ext_shared": False,
    "is_pending_ext_shared": False,
    "is_member": False,
}


@pytest.fixture
def slack(monkeypatch):
    rule = ChannelRuleSettings(prefix="inc-", prompt="", kickoff="Investigate the incident.")
    monkeypatch.setattr(
        channels.INTEGRATIONS,
        "channel_rule",
        AsyncMock(side_effect=lambda name: rule if name.startswith("inc-") else None),
    )
    client = SimpleNamespace(
        conversations_join=AsyncMock(), chat_postMessage=AsyncMock(return_value={"ts": "5.0"})
    )

    @asynccontextmanager
    async def bot():
        yield client

    monkeypatch.setattr(channels.SlackClient, "bot", bot)
    monkeypatch.setattr(SlackChannel, "fetch", AsyncMock(return_value=dict(CHANNEL)))
    monkeypatch.setattr(channels.queue, "schedule", AsyncMock())
    monkeypatch.setattr(channels, "fetch_slack_thread_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(channels, "resolve_slack_thread_id", AsyncMock(return_value="thread-1"))
    monkeypatch.setattr(channels, "upsert_agent_thread_metadata", AsyncMock(return_value=True))
    monkeypatch.setattr(channels, "create_durable_run", AsyncMock(return_value={}))
    return client


def job(kind: str, payload: dict, *, attempts: int = 0) -> Job:
    return Job(
        id=uuid.uuid7(),
        kind=kind,
        payload=payload,
        due_at=datetime.now(UTC),
        cron=None,
        timezone="UTC",
        key=None,
        metadata={},
        attempts=attempts,
        created_at=datetime.now(UTC),
    )


async def queued(kind: str) -> Job:
    [call] = [c for c in channels.queue.schedule.await_args_list if c.args[0] == kind]
    return job(kind, call.args[1])


async def test_a_new_matching_channel_is_joined_and_kicked_off_in_a_thread(slack):
    response = await channels.handle_slack_event(
        {"type": "channel_created", "channel": {"id": "C1", "name": "inc-api"}}
    )
    assert response == {"status": "accepted"}

    await channels.RuleChannel.join(await queued(channels.JOIN_JOB))
    await channels.RuleChannel.kickoff(await queued(channels.KICKOFF_JOB))

    slack.conversations_join.assert_awaited_once_with(channel="C1")
    channels.resolve_slack_thread_id.assert_awaited_once_with("C1", "5.0")
    # Readers such as an incidents app get the channel without parsing the title.
    assert channels.upsert_agent_thread_metadata.await_args.kwargs["extra_metadata"] == {
        "incident_channel": {"id": "C1", "name": "inc-api"}
    }
    run = channels.create_durable_run.await_args
    assert run.kwargs["config"]["configurable"]["slack_thread"] == {
        "channel_id": "C1",
        "thread_ts": "5.0",
    }
    task = str(run.kwargs["input"]["messages"][-1]["content"])
    assert "#inc-api" in task
    assert "Investigate the incident." in task


async def test_a_rule_without_kickoff_only_joins(slack):
    channels.INTEGRATIONS.channel_rule.side_effect = lambda name: ChannelRuleSettings(
        prefix="inc-", prompt="", kickoff=""
    )

    await channels.RuleChannel.join(job(channels.JOIN_JOB, {"channel_id": "C1", "name": "inc-api"}))

    slack.conversations_join.assert_awaited_once_with(channel="C1")
    slack.chat_postMessage.assert_not_awaited()
    channels.queue.schedule.assert_not_awaited()


async def test_a_channel_robin_already_joined_gets_no_second_kickoff(slack):
    SlackChannel.fetch.return_value = {**CHANNEL, "is_member": True}

    await channels.RuleChannel.join(job(channels.JOIN_JOB, {"channel_id": "C1", "name": "inc-api"}))

    slack.chat_postMessage.assert_not_awaited()
    channels.queue.schedule.assert_not_awaited()


async def test_a_retried_join_still_kicks_off_after_its_own_join(slack):
    SlackChannel.fetch.return_value = {**CHANNEL, "is_member": True}
    retry = job(channels.JOIN_JOB, {"channel_id": "C1", "name": "inc-api"}, attempts=1)

    await channels.RuleChannel.join(retry)

    slack.chat_postMessage.assert_awaited_once()
    assert (await queued(channels.KICKOFF_JOB)).payload["thread_ts"] == "5.0"


async def test_other_channels_fall_through_to_ordinary_slack(slack):
    other = {"type": "channel_created", "channel": {"id": "C2", "name": "eng"}}
    assert await channels.handle_slack_event(other) is None
    message = {"type": "message", "channel": "C1", "text": "hi"}
    assert await channels.handle_slack_event(message) is None


async def test_a_retried_join_skips_a_channel_shared_outside_the_workspace(slack):
    SlackChannel.fetch.return_value = {**CHANNEL, "is_member": True, "is_ext_shared": True}
    retry = job(channels.JOIN_JOB, {"channel_id": "C1", "name": "inc-api"}, attempts=1)

    await channels.RuleChannel.join(retry)

    slack.conversations_join.assert_not_awaited()
    slack.chat_postMessage.assert_not_awaited()


async def test_a_retried_join_reuses_only_its_own_recent_notice(slack, monkeypatch):
    now = datetime.now(UTC).timestamp()
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    channels.fetch_slack_thread_messages.return_value = [
        {"ts": f"{now - 86400:.6f}", "user": "UBOT", "text": channels._KICKOFF_NOTICE},
        {"ts": f"{now + 1:.6f}", "user": "UOTHERBOT", "text": channels._KICKOFF_NOTICE},
        {"ts": f"{now + 2:.6f}", "user": "UBOT", "text": channels._KICKOFF_NOTICE},
        {"ts": f"{now + 3:.6f}", "user": "UOTHERBOT", "text": channels._KICKOFF_NOTICE},
    ]
    retry = job(channels.JOIN_JOB, {"channel_id": "C1", "name": "inc-api"}, attempts=1)

    await channels.RuleChannel.join(retry)

    slack.chat_postMessage.assert_not_awaited()
    assert (await queued(channels.KICKOFF_JOB)).payload["thread_ts"] == f"{now + 2:.6f}"


@pytest.mark.parametrize("started", [True, False])
async def test_a_retried_investigation_starts_a_run_only_if_none_started(
    slack, monkeypatch, started
):
    thread = SimpleNamespace(runtime="mda")
    monkeypatch.setattr(channels.THREADS, "get", AsyncMock(return_value=thread))
    runs = SimpleNamespace(latest_run=AsyncMock(return_value=object() if started else None))
    monkeypatch.setattr(channels.RUNTIMES, "runs_on", lambda runtime: runs)
    retry = job(
        channels.KICKOFF_JOB,
        {"channel_id": "C1", "name": "inc-api", "thread_ts": "5.0", "kickoff": "Investigate."},
        attempts=1,
    )

    await channels.RuleChannel.kickoff(retry)

    assert channels.create_durable_run.await_count == int(not started)
