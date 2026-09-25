"""A bot DM, for someone who turned concierge mode on: every message routes
to the same agent thread. Off by default, where a DM keeps a thread per message.
"""

import json
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks
from starlette.requests import Request

from agent.slack import events as slack_events
from agent.slack import routes as slack_routes
from agent.slack import webhook as slack_service
from agent.slack.dm import CONCIERGE_TS
from agent.slack.payloads import SlackChannelContext
from agent.slack.request import SlackRequest
from agent.webhooks import common as webhook_common


class _FakeThreads:
    async def create(self, *, thread_id: str, **_kwargs: Any) -> None:
        return None

    async def get(self, thread_id: str) -> dict[str, str]:
        raise KeyError(thread_id)


class _FakeClient:
    def __init__(self) -> None:
        self.threads = _FakeThreads()


class _FakeBackgroundTasks:
    def __init__(self) -> None:
        self.tasks: list[tuple[Any, tuple[Any, ...]]] = []

    def add_task(self, func: Any, *args: Any) -> None:
        self.tasks.append((func, args))


class _FakeRequest:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.headers: dict[str, str] = {}
        self._body = json.dumps(payload).encode()

    async def body(self) -> bytes:
        return self._body


def _dm_payload(
    event_id: str, *, channel_type: str = "im", thread_ts: str | None = None
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "message",
        "channel": "D1",
        "channel_type": channel_type,
        "ts": "1786573369.551099",
        "user": "U1",
        "text": "fix the flaky test",
    }
    if thread_ts is not None:
        event["thread_ts"] = thread_ts
    return {
        "type": "event_callback",
        "event_id": event_id,
        "authorizations": [{"user_id": "BOT"}],
        "event": event,
    }


@pytest.fixture(autouse=True)
def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
    slack_events.reset_slack_event_claims()
    monkeypatch.setattr("agent.incidents.channels.handle_slack_event", AsyncMock(return_value=None))

    async def channel_context(_channel_id: str, *, use_cache: bool = True) -> SlackChannelContext:
        return SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=False, is_im=True)

    async def repo_config(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"owner": "langchain-ai", "name": "open-swe"}

    monkeypatch.setattr(slack_events, "get_client", lambda url: _FakeClient())
    monkeypatch.setattr(webhook_common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(webhook_common, "resolve_slack_thread_id", AsyncMock(return_value="t1"))
    monkeypatch.setattr(webhook_common, "lookup_slack_thread_id", AsyncMock(return_value="t1"))
    monkeypatch.setattr(webhook_common, "is_code_channel", AsyncMock(return_value=False))
    monkeypatch.setattr(webhook_common, "resolve_slack_channel_context", channel_context)
    monkeypatch.setattr(webhook_common, "get_slack_repo_config", repo_config)
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USER_ID", "BOT")
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USERNAME", "openswe")
    monkeypatch.setattr(slack_service, "process_slack_mention", AsyncMock())
    monkeypatch.setattr(slack_routes.User, "concierge_mode_for_slack", AsyncMock(return_value=True))


async def _queued_request(payload: dict[str, Any]) -> SlackRequest:
    background_tasks = _FakeBackgroundTasks()
    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(payload)),
        cast(BackgroundTasks, background_tasks),
    )
    assert response["status"] == "accepted", response
    return cast(SlackRequest, background_tasks.tasks[0][1][0])


async def test_untagged_dm_routes_to_the_one_concierge_thread() -> None:
    request = await _queued_request(_dm_payload("Ev-dm"))

    assert request.thread_ts == CONCIERGE_TS
    assert request.concierge_mode is True
    assert request.reply_thread_ts == ""
    assert request.treat_all_messages_as_mentions is True


async def test_every_dm_message_shares_one_agent_thread() -> None:
    first = await _queued_request(_dm_payload("Ev-dm-1"))
    second_payload = _dm_payload("Ev-dm-2")
    second_payload["event"]["ts"] = "1786573999.000100"
    second = await _queued_request(second_payload)

    assert first.thread_ts == second.thread_ts == CONCIERGE_TS


async def test_dm_thread_reply_keeps_the_session_but_answers_in_the_thread() -> None:
    request = await _queued_request(
        _dm_payload("Ev-dm-thread", thread_ts="1786573300.000000"),
    )

    assert request.thread_ts == CONCIERGE_TS
    assert request.reply_thread_ts == "1786573300.000000"


async def test_dm_keeps_a_thread_per_message_until_the_person_opts_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default: a DM behaves as it always has, but still answers untagged messages."""
    monkeypatch.setattr(
        slack_routes.User, "concierge_mode_for_slack", AsyncMock(return_value=False)
    )

    request = await _queued_request(_dm_payload("Ev-dm-off"))

    assert request.thread_ts == "1786573369.551099"
    assert request.concierge_mode is False
    assert request.treat_all_messages_as_mentions is True


async def test_channel_message_still_uses_its_own_slack_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def channel_context(_channel_id: str, *, use_cache: bool = True) -> SlackChannelContext:
        return SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=False, is_im=False)

    monkeypatch.setattr(webhook_common, "resolve_slack_channel_context", channel_context)
    payload = _dm_payload("Ev-channel", channel_type="channel")
    payload["event"]["channel"] = "C1"
    payload["event"]["text"] = "<@BOT> fix the flaky test"

    request = await _queued_request(payload)

    assert request.thread_ts == "1786573369.551099"
    assert request.concierge_mode is False


@pytest.mark.asyncio
@pytest.mark.parametrize("concierge_on", [True, False])
async def test_a_note_reaches_the_concierge_thread_only_in_concierge_mode(
    monkeypatch: pytest.MonkeyPatch, concierge_on: bool
) -> None:
    from agent.slack import dm

    monkeypatch.setattr(dm.User, "concierge_mode_for_slack", AsyncMock(return_value=concierge_on))
    lookup = AsyncMock(return_value="concierge-thread")
    monkeypatch.setattr(dm, "lookup_slack_thread_id", lookup)
    monkeypatch.setattr(dm, "langgraph_client", lambda: object())
    queue = AsyncMock(return_value=True)
    monkeypatch.setattr(dm, "queue_message_for_thread", queue)

    await dm.note_for_concierge("U1", "D1", "a note")

    if concierge_on:
        assert lookup.await_args.args[1:] == ("D1", CONCIERGE_TS)
        queue.assert_awaited_once_with("concierge-thread", [{"type": "text", "text": "a note"}])
    else:
        queue.assert_not_awaited()
