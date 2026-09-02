import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks, Request

from agent.webhooks import slack_routes

JOIN = {
    "type": "message",
    "subtype": "channel_join",
    "channel": "C-code",
    "user": "U1",
    "text": "<@U1> has joined the channel",
    "ts": "1717171717.000100",
    "event_ts": "1717171717.000100",
}


def _request(event: dict[str, Any]) -> Request:
    body = json.dumps(
        {
            "type": "event_callback",
            "event_id": "Ev1",
            "authorizations": [{"user_id": "U-BOT"}],
            "event": event,
        }
    ).encode()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {"type": "http", "method": "POST", "path": "/webhooks/slack", "headers": []}, receive
    )


@pytest.fixture
def webhook(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {
        "verify_slack_signature": lambda **_: True,
        "_get_slack_channel_context": AsyncMock(
            return_value={"id": "C-code", "is_ext_shared": False, "is_pending_ext_shared": False}
        ),
        "slack_channel_allows_operations": lambda _context: True,
        "is_code_channel": AsyncMock(return_value=True),
        "claim_slack_event": AsyncMock(return_value=True),
        "slack_event_already_seen": AsyncMock(return_value=False),
        "lookup_slack_thread_id": AsyncMock(return_value="thread-code"),
        "queue_message_for_thread": AsyncMock(return_value=True),
        "resolve_slack_thread_id": AsyncMock(return_value="thread-code"),
        "get_slack_repo_config": AsyncMock(return_value={"owner": "acme", "name": "billing"}),
        "SLACK_BOT_USER_ID": "U-BOT",
    }
    for name, mock in calls.items():
        monkeypatch.setattr(slack_routes.common, name, mock)
    dispatched = AsyncMock()
    monkeypatch.setattr(slack_routes.service, "process_slack_mention", dispatched)
    return {**calls, "process_slack_mention": dispatched}


async def _post(event: dict[str, Any]) -> tuple[dict[str, str], BackgroundTasks]:
    tasks = BackgroundTasks()
    response = await slack_routes.slack_webhook(_request(event), tasks)
    for task in tasks.tasks:
        await task()
    return response, tasks


async def test_a_join_does_not_start_a_run(webhook: dict[str, Any]) -> None:
    """A code channel answers every message, and an invite is not a request."""
    response, _ = await _post(JOIN)

    assert response["reason"] == "Slack channel housekeeping, not a request"
    webhook["process_slack_mention"].assert_not_awaited()


async def test_a_join_waits_in_the_queue_for_the_next_turn(webhook: dict[str, Any]) -> None:
    await _post(JOIN)

    webhook["queue_message_for_thread"].assert_awaited_once()
    thread_id, blocks = webhook["queue_message_for_thread"].await_args.args
    assert thread_id == "thread-code"
    assert "<@U1> has joined the channel" in blocks[0]["text"]
    assert "Nothing is being asked of you" in blocks[0]["text"]


@pytest.mark.parametrize(
    "subtype",
    ["channel_leave", "group_join", "channel_topic", "channel_name", "channel_purpose"],
)
async def test_the_channel_narrating_itself_is_never_a_request(
    webhook: dict[str, Any], subtype: str
) -> None:
    response, _ = await _post({**JOIN, "subtype": subtype})

    assert response["reason"] == "Slack channel housekeeping, not a request"
    webhook["process_slack_mention"].assert_not_awaited()


async def test_housekeeping_outside_a_code_channel_is_simply_ignored(
    webhook: dict[str, Any],
) -> None:
    """A thread session has no interest in who joined the channel around it."""
    webhook["is_code_channel"].return_value = False

    await _post(JOIN)

    webhook["queue_message_for_thread"].assert_not_awaited()
    webhook["process_slack_mention"].assert_not_awaited()


async def test_a_join_in_an_unbound_channel_queues_nothing(webhook: dict[str, Any]) -> None:
    """No session yet means nobody to tell; the channel's own creation covers it."""
    webhook["lookup_slack_thread_id"].return_value = None

    await _post(JOIN)

    webhook["queue_message_for_thread"].assert_not_awaited()


async def test_an_ordinary_message_still_reaches_the_session(webhook: dict[str, Any]) -> None:
    await _post({**JOIN, "subtype": None, "text": "what is the status?"})

    webhook["process_slack_mention"].assert_awaited_once()
    webhook["queue_message_for_thread"].assert_not_awaited()
