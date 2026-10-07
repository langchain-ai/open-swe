import json
from typing import Any
from unittest.mock import ANY, AsyncMock
from urllib.parse import urlencode

import pytest
from fastapi import BackgroundTasks, Request

from agent.slack import routes as slack_routes
from agent.slack.payloads import SlackBlockAction, SlackChannelContext, SlackInteraction
from agent.threads.admin_approval import _blocks


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
    update = AsyncMock(return_value=None)
    monkeypatch.setattr(slack_routes.common, "update_slack_message", update)

    await slack_routes._update_selected_option_message(
        SlackInteraction.model_validate(payload),
        SlackBlockAction.model_validate(payload["actions"][0]),
        "Option B",
    )

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
        "resolve_slack_channel_context",
        AsyncMock(return_value=SlackChannelContext(is_ext_shared=True)),
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
        "resolve_slack_channel_context",
        AsyncMock(
            return_value=SlackChannelContext(
                name="proj-open-swe", is_ext_shared=False, is_pending_ext_shared=False
            )
        ),
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
    update.assert_awaited_once_with(
        SlackInteraction.model_validate(payload),
        SlackBlockAction.model_validate(payload["actions"][0]),
        "Option B",
    )
    process.assert_awaited_once()


@pytest.mark.parametrize(
    ("decision", "authorized", "status"),
    [("approve", False, "ignored"), ("approve", True, "accepted"), ("reject", True, "accepted")],
)
@pytest.mark.parametrize("parent_in_container", [False, True])
async def test_admin_approval_uses_actual_location_and_only_queues_authorized_owner(
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
    authorized: bool,
    status: str,
    parent_in_container: bool,
) -> None:
    payload = _option_payload()
    if parent_in_container:
        payload["container"] = {"thread_ts": payload["message"].pop("thread_ts")}
    blocks = _blocks("privileged_tool", '{"secret_argument":"private value"}', "request-1")
    actions = blocks[-1]["elements"]
    assert isinstance(actions, list)
    action = dict(actions[0 if decision == "approve" else 1])
    action["action_ts"] = "3.0"
    button = json.loads(action["value"])
    button.update(thread_id="forged-thread", thread_ts="forged-ts")
    action["value"] = json.dumps(button)
    payload["actions"] = [action]
    payload["message"]["blocks"] = blocks
    payload["message"]["text"] = "private value"
    payload["response_url"] = "https://hooks.slack.com/actions/test"
    decide = AsyncMock(return_value=authorized)
    update = AsyncMock(return_value=True)
    public_update = AsyncMock()
    process = AsyncMock()
    ephemeral = AsyncMock()
    public_reply = AsyncMock()
    lookup = AsyncMock(return_value="thread-1")
    monkeypatch.setattr(slack_routes.common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(slack_routes, "get_langgraph_client", lambda: object())
    monkeypatch.setattr(slack_routes.common, "lookup_slack_thread_id", lookup)
    monkeypatch.setattr(
        slack_routes.common,
        "resolve_slack_channel_context",
        AsyncMock(
            return_value=SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=False)
        ),
    )
    monkeypatch.setattr(
        slack_routes.common,
        "get_slack_repo_config",
        AsyncMock(return_value={"owner": "langchain-ai", "name": "open-swe"}),
    )
    monkeypatch.setattr(slack_routes, "decide_admin_approval", decide)
    monkeypatch.setattr(slack_routes, "respond_to_slack_interaction", update)
    monkeypatch.setattr(slack_routes.common, "update_slack_message", public_update)
    monkeypatch.setattr(slack_routes.service, "process_slack_mention", process)
    monkeypatch.setattr(slack_routes.common, "post_slack_ephemeral_message", ephemeral)
    monkeypatch.setattr(slack_routes.common, "post_slack_thread_reply", public_reply)
    tasks = BackgroundTasks()

    result = await slack_routes.slack_interactivity(_request(payload), tasks)
    await tasks()

    assert result.get("status") == status
    lookup.assert_awaited_once_with(ANY, "C1", "1.0")
    decide.assert_awaited_once_with(
        "thread-1",
        "request-1",
        slack_user_id="U1",
        channel_id="C1",
        thread_ts="1.0",
        approved=decision == "approve",
    )
    public_reply.assert_not_awaited()
    public_update.assert_not_awaited()
    if not authorized:
        ephemeral.assert_awaited_once()
        update.assert_not_awaited()
        process.assert_not_awaited()
    else:
        ephemeral.assert_not_awaited()
        message = (
            "Admin action approved; retry queued."
            if decision == "approve"
            else "Admin action rejected."
        )
        update.assert_awaited_once_with(
            "https://hooks.slack.com/actions/test",
            {
                "replace_original": True,
                "response_type": "ephemeral",
                "text": message,
                "blocks": [{"type": "section", "text": {"type": "plain_text", "text": message}}],
            },
        )
        if decision == "approve":
            process.assert_awaited_once()
            assert process.await_args is not None
            request = process.await_args.args[0]
            assert request.user_id == "U1"
            assert request.thread_id == "thread-1"
            assert request.event_ts == "3.0"
            assert request.thread_ts == "1.0"
        else:
            process.assert_not_awaited()


@pytest.mark.asyncio
async def test_code_channel_view_action_routes_to_channel_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "type": "block_actions",
        "trigger_id": "trigger-view-1",
        "container": {
            "type": "code_channel_view",
            "channel_id": "C-code",
            "view_id": "V-plan",
        },
        "user": {"id": "U1"},
        "actions": [
            {
                "type": "button",
                "action_id": "approve-plan",
                "action_ts": "1786574000.000003",
                "value": "approve",
            }
        ],
    }
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
        "resolve_slack_channel_context",
        AsyncMock(return_value=SlackChannelContext()),
    )
    monkeypatch.setattr(
        slack_routes.common,
        "get_slack_repo_config",
        AsyncMock(return_value={"owner": "langchain-ai", "name": "open-swe"}),
    )
    monkeypatch.setattr(slack_routes.service, "process_slack_mention", process)
    background_tasks = BackgroundTasks()

    result = await slack_routes.slack_interactivity(_request(payload), background_tasks)
    await background_tasks()

    assert result.get("status") == "accepted"
    assert process.await_args is not None
    event_data = process.await_args.args[0]
    assert event_data.thread_ts == "0"
    assert event_data.explicit_request is True
    assert "approve-plan" in event_data.text


@pytest.mark.asyncio
async def test_code_channel_external_select_returns_registered_suggestions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "type": "block_suggestion",
        "container": {
            "type": "code_channel_view",
            "channel_id": "C-code",
            "view_id": "V-plan",
        },
        "action_id": "repository",
        "value": "open",
    }
    options = [{"text": {"type": "plain_text", "text": "open-swe"}, "value": "open-swe"}]
    get_suggestions = AsyncMock(return_value=options)
    monkeypatch.setattr(slack_routes.common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(slack_routes, "get_langgraph_client", lambda: object())
    monkeypatch.setattr(slack_routes.common, "get_block_suggestions", get_suggestions)

    result = await slack_routes.slack_interactivity(_request(payload), BackgroundTasks())

    assert result == {"options": options}
    get_suggestions.assert_awaited_once_with(ANY, "C-code", "V-plan", "repository", "open")
