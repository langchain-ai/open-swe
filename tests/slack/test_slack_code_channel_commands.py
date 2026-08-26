import json
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from fastapi import BackgroundTasks, Request

from agent.webhooks import slack_routes


def _request(path: str, body: bytes) -> Request:
    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {"type": "http", "method": "POST", "path": path, "headers": []},
        receive,
    )


@pytest.fixture
def code_channel_route(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    process = AsyncMock()
    monkeypatch.setattr(slack_routes.common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(slack_routes.common, "is_code_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(slack_routes, "get_langgraph_client", lambda: object())
    monkeypatch.setattr(
        slack_routes.common, "lookup_slack_thread_id", AsyncMock(return_value="thread-1")
    )
    monkeypatch.setattr(slack_routes.common, "claim_slack_event", AsyncMock(return_value=True))
    monkeypatch.setattr(
        slack_routes.common,
        "_get_slack_channel_context",
        AsyncMock(return_value={"name": "code-task"}),
    )
    monkeypatch.setattr(
        slack_routes.common,
        "get_slack_repo_config",
        AsyncMock(return_value={"owner": "langchain-ai", "name": "open-swe"}),
    )
    monkeypatch.setattr(
        slack_routes.common, "increment_slack_thread_version", AsyncMock(return_value=4)
    )
    monkeypatch.setattr(slack_routes.common, "SLACK_BOT_USER_ID", "BOT")
    monkeypatch.setattr(slack_routes.service, "process_slack_mention", process)
    monkeypatch.setattr(slack_routes, "_synthetic_slack_ts", lambda: "1786574000.000001")
    return process


async def test_runtime_slash_command_routes_to_code_channel_session(
    code_channel_route: AsyncMock,
) -> None:
    body = urlencode(
        {
            "channel_id": "C-code",
            "user_id": "U1",
            "command": "/run-tests",
            "text": "tests/slack",
            "trigger_id": "trigger-1",
        }
    ).encode()
    background_tasks = BackgroundTasks()

    response = await slack_routes.slack_code_channel_command(
        _request("/webhooks/slack/code-channel-commands", body), background_tasks
    )
    await background_tasks()

    assert response == {"response_type": "ephemeral", "text": "Working on /run-tests…"}
    assert code_channel_route.await_args is not None
    event_data = code_channel_route.await_args.args[0]
    assert event_data["thread_ts"] == "0"
    assert event_data["thread_version"] == 4
    assert event_data["explicit_request"] is True
    assert "/run-tests tests/slack" in event_data["text"]


async def test_context_bar_action_routes_to_code_channel_session(
    code_channel_route: AsyncMock,
) -> None:
    payload = {
        "type": "event_callback",
        "event_id": "EvAction",
        "event": {
            "type": "code_channel_action",
            "channel": "C-code",
            "user": "U1",
            "event_ts": "1786574000.000002",
            "action": {"key": "create-pr", "label": "Create PR"},
        },
    }
    background_tasks = BackgroundTasks()

    response = await slack_routes.slack_webhook(
        _request("/webhooks/slack", json.dumps(payload).encode()), background_tasks
    )
    await background_tasks()

    assert response["status"] == "accepted"
    assert code_channel_route.await_args is not None
    event_data = code_channel_route.await_args.args[0]
    assert "context-bar action" in event_data["text"]
    assert "create-pr" in event_data["text"]
