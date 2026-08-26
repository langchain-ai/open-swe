import json
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from fastapi import BackgroundTasks, Request

from agent.webhooks import slack_routes


def _request(payload: dict[str, Any]) -> Request:
    body = urlencode({"payload": json.dumps(payload)}).encode()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/webhooks/slack/interactivity",
            "headers": [],
        },
        receive,
    )


def _option_payload() -> dict[str, Any]:
    action = {
        "action_id": "open_swe_option_select_1",
        "action_ts": "3.0",
        "text": {"type": "plain_text", "text": "Option B"},
        "value": json.dumps({"type": "open_swe_option", "response": "Option B"}),
    }
    return {
        "actions": [action],
        "channel": {"id": "C1"},
        "message": {
            "ts": "2.0",
            "thread_ts": "1.0",
            "text": "Pick one",
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": "Pick one"}},
                {"type": "actions", "elements": [action]},
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": "Open in Web"}],
                },
            ],
        },
        "user": {"id": "U1"},
    }


@pytest.mark.asyncio
async def test_selected_option_updates_original_message(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _option_payload()
    update = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(slack_routes.common, "update_slack_message", update)

    await slack_routes._update_selected_option_message(payload, payload["actions"][0], "Option B")

    update.assert_awaited_once_with(
        "C1",
        "2.0",
        "Pick one",
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": "Pick one"}},
            {
                "type": "context",
                "elements": [{"type": "plain_text", "text": "Selected: Option B"}],
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "Open in Web"}],
            },
        ],
    )


@pytest.mark.asyncio
async def test_external_channel_interaction_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _option_payload()
    lookup = AsyncMock(return_value="thread-1")
    monkeypatch.setattr(slack_routes.common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(slack_routes.common, "lookup_slack_thread_id", lookup)
    monkeypatch.setattr(
        slack_routes.common,
        "_get_slack_channel_context",
        AsyncMock(return_value={"is_ext_shared": True}),
    )
    background_tasks = BackgroundTasks()

    result = await slack_routes.slack_interactivity(_request(payload), background_tasks)

    assert result == {"status": "ignored", "reason": "Slack channel is not eligible"}
    assert background_tasks.tasks == []
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_option_interaction_schedules_update_before_agent_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _option_payload()
    update = AsyncMock()
    process = AsyncMock()
    monkeypatch.setattr(slack_routes.common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(slack_routes, "get_langgraph_client", lambda: object())
    monkeypatch.setattr(
        slack_routes.common, "lookup_slack_thread_id", AsyncMock(return_value="thread-1")
    )
    monkeypatch.setattr(
        slack_routes.common,
        "_get_slack_channel_context",
        AsyncMock(return_value={"name": "proj-open-swe", "is_ext_shared": False}),
    )
    monkeypatch.setattr(
        slack_routes.common,
        "get_slack_repo_config",
        AsyncMock(return_value={"owner": "langchain-ai", "name": "open-swe"}),
    )
    monkeypatch.setattr(slack_routes, "_update_selected_option_message", update)
    monkeypatch.setattr(slack_routes.service, "process_slack_mention", process)
    background_tasks = BackgroundTasks()

    result = await slack_routes.slack_interactivity(_request(payload), background_tasks)

    assert result == {"status": "accepted", "message": "Slack option queued"}
    assert [task.func for task in background_tasks.tasks] == [update, process]
    await background_tasks()
    update.assert_awaited_once_with(payload, payload["actions"][0], "Option B")
    process.assert_awaited_once()
