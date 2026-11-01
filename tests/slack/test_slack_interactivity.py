import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from fastapi import BackgroundTasks, Request

from openswe.slack import retry
from openswe.slack import routes as slack_routes
from openswe.slack.blocks import option_actions
from openswe.slack.client import SlackConversation, SlackMessageLocation
from openswe.slack.payloads import SlackBlockAction, SlackChannelContext, SlackInteraction
from tests.conftest import MemoryEventClaims
from tests.slack_helpers import at_location


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


@pytest.mark.asyncio
async def test_explain_shortcut_preserves_selected_message_and_private_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_routes.common, "verify_slack_signature", lambda **_kwargs: True)
    process = AsyncMock()
    monkeypatch.setattr(slack_routes, "process_slack_ask", process)
    background = BackgroundTasks()
    payload = {
        "type": "message_action",
        "callback_id": "open_swe_explain",
        "trigger_id": "trigger-1",
        "response_url": "https://hooks.slack.com/actions/T1/1/x",
        "team": {"id": "T1"},
        "channel": {"id": "C1"},
        "user": {"id": "U1"},
        "message": {"ts": "2.0", "thread_ts": "1.0", "text": "Ship the migration"},
    }

    assert await slack_routes.slack_interactivity(_request(payload), background) == {}
    await background()

    selected = process.await_args.args[0]
    assert selected.selected_message == "Ship the migration"
    assert selected.user_id == "U1"
    assert selected.response_url == payload["response_url"]
    assert selected.message_ts == "2.0"
    assert not selected.by_the_way


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
    monkeypatch.setattr(SlackMessageLocation, "update", at_location(update))

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
@pytest.mark.parametrize("recipient", [None, "U1", "U2"])
async def test_option_interaction_schedules_update_only_for_authorized_actor(
    monkeypatch: pytest.MonkeyPatch,
    recipient: str | None,
) -> None:
    payload = _option_payload()
    actions = option_actions(["Option B"], user_id=recipient)
    action = actions[0]["elements"][0]
    payload["actions"][0]["value"] = action["value"]
    payload["message"]["blocks"][1]["elements"][0]["value"] = action["value"]
    notify = AsyncMock()
    repo = AsyncMock(return_value={"owner": "langchain-ai", "name": "open-swe"})
    monkeypatch.setattr(SlackConversation, "post_ephemeral", at_location(notify))
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
    monkeypatch.setattr(slack_routes.common, "get_slack_repo_config", repo)
    monkeypatch.setattr(slack_routes, "_update_selected_option_message", update)
    monkeypatch.setattr(slack_routes.service, "process_slack_mention", process)
    background_tasks = BackgroundTasks()

    result = await slack_routes.slack_interactivity(_request(payload), background_tasks)

    if recipient == "U2":
        assert result == {
            "status": "ignored",
            "reason": "Only the choice recipient can select an option",
        }
        assert background_tasks.tasks == []
        repo.assert_not_awaited()
        update.assert_not_awaited()
        process.assert_not_awaited()
        notify.assert_awaited_once_with(
            "C1", "U1", "This choice is for someone else. No action was taken.", thread_ts="1.0"
        )
        return
    notify.assert_not_awaited()
    assert result == {"status": "accepted", "message": "Slack option queued"}
    assert [task.func for task in background_tasks.tasks] == [update, process]
    await background_tasks()
    update.assert_awaited_once_with(
        SlackInteraction.model_validate(payload),
        SlackBlockAction.model_validate(payload["actions"][0]),
        "Option B",
    )
    process.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", [True, False])
async def test_next_in_a_review_guide_counts_only_for_its_reader(
    monkeypatch: pytest.MonkeyPatch, reader: bool
) -> None:
    payload = _option_payload()
    payload["actions"][0]["value"] = json.dumps({"type": "open_swe_option", "response": "Next"})
    update = AsyncMock()
    advance = AsyncMock()
    guide = SimpleNamespace(is_reader=AsyncMock(return_value=reader))
    monkeypatch.setattr(slack_routes.common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(slack_routes, "get_langgraph_client", lambda: object())
    monkeypatch.setattr(slack_routes.common, "is_code_channel", AsyncMock(return_value=True))
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
        slack_routes.ReviewGuideSession, "for_channel", AsyncMock(return_value=guide)
    )
    monkeypatch.setattr(slack_routes, "_update_selected_option_message", update)
    monkeypatch.setattr(slack_routes, "advance", advance)
    background_tasks = BackgroundTasks()

    result = await slack_routes.slack_interactivity(_request(payload), background_tasks)

    guide.is_reader.assert_awaited_once_with("U1")
    if not reader:
        assert result["status"] == "ignored"
        assert background_tasks.tasks == []
        return
    assert [task.func for task in background_tasks.tasks] == [update, advance]
    await background_tasks()
    advance.assert_awaited_once_with("C1", "2.0")


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

    assert result["status"] == "accepted"
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
    get_suggestions.assert_awaited_once_with("C-code", "V-plan", "repository", "open")


@pytest.mark.parametrize(
    "channel_id,thread_ts,message_ts",
    [("C1", "1.0", "1.5"), ("C1", "0", "1.5"), ("D1", "0", "1.5"), ("C1", "1.0", "")],
)
async def test_retry_is_requester_scoped_and_dispatches_only_once(
    monkeypatch: pytest.MonkeyPatch,
    memory_event_claims: MemoryEventClaims,
    channel_id: str,
    thread_ts: str,
    message_ts: str,
) -> None:
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "retry-test-secret-at-least-32-bytes")
    context = SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=False)
    monkeypatch.setattr(slack_routes.common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(
        slack_routes.common, "resolve_slack_channel_context", AsyncMock(return_value=context)
    )
    monkeypatch.setattr(
        slack_routes.common, "resolve_slack_thread_id", AsyncMock(return_value="thread-1")
    )
    monkeypatch.setattr(slack_routes.common, "get_slack_repo_config", AsyncMock(return_value=None))
    monkeypatch.setattr(
        slack_routes.common,
        "is_code_channel",
        AsyncMock(return_value=thread_ts == "0" and channel_id.startswith("C")),
    )
    monkeypatch.setattr(retry, "is_kitchen_channel", AsyncMock(return_value=False))
    update = AsyncMock()
    monkeypatch.setattr(SlackMessageLocation, "update", at_location(update))
    notify = AsyncMock()
    monkeypatch.setattr(SlackConversation, "post_ephemeral", at_location(notify))
    fetch = AsyncMock(return_value={"user": "U1", "text": "Fix the bug", "ts": message_ts})
    monkeypatch.setattr(retry, "fetch_slack_thread_message_by_ts", fetch)
    monkeypatch.setattr(retry, "fetch_slack_message_by_ts", fetch)
    process = AsyncMock()
    monkeypatch.setattr(slack_routes.service, "process_slack_mention", process)
    retry_request = retry.SlackRetry(
        channel_id=channel_id,
        thread_ts=thread_ts,
        user_id="U1",
        message_ts=message_ts,
        nonce="failure-1",
    )
    blocks = retry_request.blocks("Unexpected error")
    assert blocks is not None
    payload = _option_payload()
    payload["channel"]["id"] = channel_id
    payload["actions"][0]["value"] = blocks[1]["elements"][0]["value"]
    payload["message"]["blocks"] = blocks
    payload["user"]["id"] = "U2"
    background = BackgroundTasks()
    assert (await slack_routes.slack_interactivity(_request(payload), background)).get(
        "status"
    ) == "ignored"
    assert not background.tasks
    update.assert_not_awaited()
    notify.assert_awaited_once()

    payload["user"]["id"] = "U1"
    assert (await slack_routes.slack_interactivity(_request(payload), background)).get(
        "status"
    ) == "accepted"
    await background()
    assert process.await_args is not None
    request = process.await_args.args[0]
    assert request.user_id == "U1"
    assert request.thread_ts == thread_ts
    assert request.text == ("Fix the bug" if message_ts else "Retry")
    assert request.turn_context == ("" if message_ts else retry.prompt("runs/slack-retry").strip())
    assert request.event_ts != message_ts
    assert request.original_message_ts == message_ts
    assert request.explicit_request
    assert request.code_channel == (thread_ts == "0" and channel_id.startswith("C"))
    assert request.concierge_mode == (channel_id.startswith("D") and thread_ts == "0")
    assert (await slack_routes.slack_interactivity(_request(payload), BackgroundTasks())).get(
        "status"
    ) == "ignored"
    process.assert_awaited_once()
