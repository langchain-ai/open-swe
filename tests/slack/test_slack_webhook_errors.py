import json
from typing import Any
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlencode

import pytest
from fastapi import BackgroundTasks, Request
from starlette.types import Message

from agent.run_config import Repo
from agent.slack import failures as slack_failures
from agent.slack import routes as slack_routes
from agent.slack import webhook as slack_webhook
from agent.slack.payloads import SlackChannelContext
from agent.slack.request import SlackRequest
from agent.webhooks import common as webhook_common


class _FakeThreads:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        self.updates.append({"thread_id": thread_id, "metadata": metadata})


class _FakeClient:
    def __init__(self) -> None:
        self.threads = _FakeThreads()


def _event_data() -> SlackRequest:
    return SlackRequest(
        channel_id="C1",
        thread_ts="123.45",
        event_ts="123.45",
        user_id="U1",
        text="help",
        bot_user_id="BOT",
    )


@pytest.mark.asyncio
async def test_slack_processing_error_marks_thread_and_replies_with_error_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_processing(event_data: dict[str, Any], repo_config: dict[str, str]) -> None:
        raise RuntimeError("boom")

    client = _FakeClient()
    upsert = AsyncMock()
    post_reply = AsyncMock(return_value=True)
    show_status = AsyncMock(return_value=True)
    clear_status = AsyncMock(return_value=True)

    monkeypatch.setattr(slack_webhook, "_process_slack_mention_impl", fail_processing)
    monkeypatch.setattr(slack_webhook, "restore_slack_thinking_status", show_status)
    monkeypatch.setattr(slack_webhook, "clear_slack_thinking_status_if_idle", clear_status)
    monkeypatch.setattr(
        slack_webhook.common, "lookup_slack_thread_id", AsyncMock(return_value="t1")
    )
    monkeypatch.setattr(
        slack_webhook.common, "strip_bot_mention", lambda text, *_args, **_kwargs: text
    )
    monkeypatch.setattr(slack_webhook.common, "upsert_agent_thread_metadata", upsert)
    monkeypatch.setattr(slack_webhook, "get_langgraph_client", lambda: client)
    monkeypatch.setattr(slack_failures, "post_slack_thread_reply", post_reply)
    monkeypatch.setattr(slack_webhook.User, "login_for_slack", AsyncMock(return_value="alice"))

    await slack_webhook.process_slack_mention(
        _event_data(),
        webhook_common.SlackRepoResolution(Repo(owner="langchain-ai", name="open-swe")),
    )

    upsert.assert_awaited_once()
    assert len(client.threads.updates) == 1
    update = client.threads.updates[0]
    assert update["thread_id"] == "t1"
    assert update["metadata"]["latest_run_status"] == "error"
    assert "failure_reply_posted" not in update["metadata"]
    assert isinstance(update["metadata"]["updated_at_ms"], int)
    show_status.assert_awaited_once_with("C1", "123.45")
    clear_status.assert_awaited_once_with(client, "t1", "C1", "123.45")
    post_reply.assert_awaited_once()
    await_args = post_reply.await_args
    assert await_args is not None
    assert await_args.args[:2] == ("C1", "123.45")
    assert "Error ID: `" in await_args.args[2]
    assert await_args.kwargs["agent_thread_id"] == "t1"


@pytest.mark.asyncio
async def test_slack_message_update_does_not_claim_thinking_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = AsyncMock(return_value=False)
    show_status = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_webhook, "_process_slack_mention_impl", process)
    monkeypatch.setattr(slack_webhook, "restore_slack_thinking_status", show_status)

    await slack_webhook.process_slack_mention(
        _event_data().model_copy(update={"message_update": True}), None
    )

    show_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_concierge_dm_does_not_show_thinking_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = AsyncMock(return_value=True)
    show_status = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_webhook, "_process_slack_mention_impl", process)
    monkeypatch.setattr(slack_webhook, "restore_slack_thinking_status", show_status)

    await slack_webhook.process_slack_mention(
        _event_data().model_copy(update={"concierge_mode": True}), None
    )

    process.assert_awaited_once()
    show_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_slack_processing_error_replies_even_without_an_agent_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_processing(event_data: dict[str, Any], repo_config: dict[str, str]) -> None:
        raise RuntimeError("boom")

    post_reply = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_webhook, "_process_slack_mention_impl", fail_processing)
    monkeypatch.setattr(
        slack_webhook.common, "lookup_slack_thread_id", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(slack_webhook, "get_langgraph_client", lambda: _FakeClient())
    monkeypatch.setattr(slack_failures, "post_slack_thread_reply", post_reply)

    await slack_webhook.process_slack_mention(
        _event_data(),
        webhook_common.SlackRepoResolution(Repo(owner="langchain-ai", name="open-swe")),
    )

    post_reply.assert_awaited_once()
    await_args = post_reply.await_args
    assert await_args is not None
    assert await_args.args[:2] == ("C1", "123.45")
    assert await_args.kwargs["agent_thread_id"] is None


@pytest.mark.parametrize("action", ["approve", "revise", "cancel"])
@pytest.mark.parametrize("thread_ts", ["123.45", None])
async def test_legacy_plan_buttons_only_notify_the_clicking_user(
    monkeypatch: pytest.MonkeyPatch, action: str, thread_ts: str | None
) -> None:
    payload = {
        "type": "block_actions",
        "channel": {"id": "C1" if thread_ts else "D1"},
        "user": {"id": "U1"},
        "message": {"ts": "234.56", **({"thread_ts": thread_ts} if thread_ts else {})},
        "actions": [
            {
                "action_id": "open_swe_option_select_0" if thread_ts else "open_swe_option_select",
                "value": json.dumps({"type": "plan_approval", "action": action}),
            }
        ],
    }
    body = urlencode({"payload": json.dumps(payload)}).encode()

    async def receive() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request({"type": "http", "headers": []}, receive)
    notify = AsyncMock(return_value=True)
    client = Mock(side_effect=AssertionError("Retired buttons must not access thread state"))
    dispatch = AsyncMock()
    monkeypatch.setattr(slack_routes.common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(slack_routes.common, "post_slack_ephemeral_message", notify)
    monkeypatch.setattr(slack_routes, "get_langgraph_client", client)
    monkeypatch.setattr(slack_routes.service, "process_slack_mention", dispatch)
    tasks = BackgroundTasks()

    result = await slack_routes.slack_interactivity(request, tasks)
    await tasks()

    assert result["status"] == "accepted"
    notify.assert_awaited_once()
    args = notify.await_args
    assert args is not None
    assert args.args[:2] == ("C1" if thread_ts else "D1", "U1")
    assert args.kwargs == {"thread_ts": thread_ts}
    assert "no longer active" in args.args[2]
    assert "artifact" in args.args[2]
    assert "reply in this thread" in args.args[2]
    assert "No action was taken" in args.args[2]
    client.assert_not_called()
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_or_queue_enqueues_untagged_follow_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch = AsyncMock(return_value={"run_id": "run-1"})
    monkeypatch.setattr(slack_webhook.common, "dispatch_agent_run", dispatch)

    blocks = [{"type": "text", "text": "follow up"}]
    run = await slack_webhook._dispatch_or_queue_slack_run(
        _FakeClient(),
        "t1",
        blocks,
        {},
        explicitly_tagged=False,
        trigger_ts="1700000000.000100",
    )

    assert run == {"run_id": "run-1"}
    await_args = dispatch.await_args
    assert await_args is not None
    assert await_args.args[1] is None
    assert await_args.kwargs["input"] == {"messages": blocks}
    assert await_args.kwargs["multitask_strategy"] == "enqueue"
    assert await_args.kwargs["metadata"]["slack_trigger_ts"] == "1700000000.000100"


@pytest.mark.asyncio
async def test_message_update_dispatches_a_new_message_without_old_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    fetch_messages = AsyncMock(return_value=[{"ts": "1.0", "user": "U1", "text": "old text"}])
    dispatch = AsyncMock(return_value={"run_id": "run-1"})
    store_mapping = AsyncMock()
    monkeypatch.setattr(slack_webhook.common, "authorize_github_thread", AsyncMock(return_value={}))
    monkeypatch.setattr(slack_webhook, "get_langgraph_client", lambda: client)
    monkeypatch.setattr(slack_webhook.common, "get_slack_user_info", AsyncMock(return_value=None))
    monkeypatch.setattr(slack_webhook.common, "fetch_slack_thread_messages", fetch_messages)
    monkeypatch.setattr(slack_webhook.common, "get_slack_user_names", AsyncMock(return_value={}))
    monkeypatch.setattr(
        slack_webhook.common,
        "resolve_slack_links_in_context",
        AsyncMock(return_value=("", [])),
    )
    monkeypatch.setattr(slack_webhook.User, "login_for_slack", AsyncMock(return_value="alice"))
    monkeypatch.setattr(
        slack_webhook.common, "get_valid_access_token", AsyncMock(return_value="tok")
    )
    monkeypatch.setattr(slack_webhook.common, "thread_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(slack_webhook.common, "get_thread_workspace", AsyncMock(return_value=None))
    monkeypatch.setattr(
        slack_webhook.common, "get_thread_model_choice", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(slack_webhook.common, "upsert_agent_thread_metadata", AsyncMock())
    monkeypatch.setattr(slack_webhook, "queue_message_for_thread", AsyncMock(return_value=False))
    monkeypatch.setattr(slack_webhook, "_dispatch_or_queue_slack_run", dispatch)
    thinking = AsyncMock()
    monkeypatch.setattr(slack_webhook, "stream_slack_thinking_steps", thinking)
    monkeypatch.setattr(slack_webhook.common, "store_slack_run_mapping", store_mapping)

    await slack_webhook._process_slack_mention_impl(
        SlackRequest(
            channel_id="C1",
            channel_context={},
            thread_ts="1.0",
            event_ts="2.0",
            original_message_ts="1.0",
            user_id="U1",
            text="new corrected text",
            bot_user_id="BOT",
            thread_id="t1",
            message_update=True,
        ),
        webhook_common.SlackRepoResolution(
            Repo(owner="langchain-ai", name="open-swe"), explicit=True
        ),
    )

    fetch_messages.assert_not_awaited()
    await_args = dispatch.await_args
    assert await_args is not None
    run_input = await_args.args[2]
    serialized = str(run_input["messages"])
    assert "new corrected text" in serialized
    assert "old text" not in serialized
    assert "## Conversation Context" not in serialized
    assert await_args.kwargs["explicitly_tagged"] is False
    thinking.assert_not_awaited()
    store_args = store_mapping.await_args
    assert store_args is not None
    assert store_args.kwargs["message_ts"] == "1.0"
    assert store_args.kwargs["agent_thread_id"] == "t1"


@pytest.mark.asyncio
async def test_private_dm_does_not_dispatch_when_privacy_metadata_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_webhook.common, "authorize_github_thread", AsyncMock(return_value={}))
    dispatch = AsyncMock(return_value={"run_id": "run-1"})
    monkeypatch.setattr(slack_webhook, "get_langgraph_client", lambda: _FakeClient())
    monkeypatch.setattr(slack_webhook.common, "get_slack_user_info", AsyncMock(return_value=None))
    monkeypatch.setattr(
        slack_webhook.common, "fetch_slack_thread_messages", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(slack_webhook.common, "get_slack_user_names", AsyncMock(return_value={}))
    monkeypatch.setattr(
        slack_webhook.common, "resolve_slack_links_in_context", AsyncMock(return_value=("", []))
    )
    monkeypatch.setattr(slack_webhook.User, "login_for_slack", AsyncMock(return_value="alice"))
    monkeypatch.setattr(
        slack_webhook.common, "get_valid_access_token", AsyncMock(return_value="tok")
    )
    monkeypatch.setattr(slack_webhook.common, "thread_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(slack_webhook.common, "get_thread_workspace", AsyncMock(return_value=None))
    monkeypatch.setattr(
        slack_webhook.common, "get_thread_model_choice", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        slack_webhook.common, "upsert_agent_thread_metadata", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(slack_webhook, "_dispatch_or_queue_slack_run", dispatch)

    with pytest.raises(RuntimeError):
        await slack_webhook._process_slack_mention_impl(
            SlackRequest(
                channel_id="D1",
                channel_context={"is_im": True},
                thread_ts="1.0",
                event_ts="1.0",
                user_id="U1",
                text="hello",
                bot_user_id="BOT",
                thread_id="t1",
            ),
            webhook_common.SlackRepoResolution(
                Repo(owner="langchain-ai", name="open-swe"), explicit=True
            ),
        )
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_errored_dm_owner_falls_back_to_email_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upsert = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_webhook.common, "upsert_agent_thread_metadata", upsert)
    monkeypatch.setattr(slack_webhook, "get_langgraph_client", lambda: _FakeClient())
    monkeypatch.setattr(
        slack_webhook.common, "strip_bot_mention", lambda text, *_args, **_kwargs: text
    )
    monkeypatch.setattr(slack_webhook.User, "login_for_slack", AsyncMock(return_value=None))
    monkeypatch.setattr(
        slack_webhook.common,
        "get_slack_user_info",
        AsyncMock(return_value={"profile": {"email": "alice@example.com"}}),
    )
    monkeypatch.setattr(slack_webhook.User, "login_for_email", AsyncMock(return_value="alice"))

    request = _event_data().model_copy(update={"channel_context": SlackChannelContext(is_im=True)})
    await slack_webhook._mark_slack_thread_errored("t1", request, None)

    kwargs = upsert.await_args.kwargs
    assert kwargs["visibility"] == "private"
    assert kwargs["owner_login"] == "alice"

    # An unlinked DM sender never reaches thread creation, so the error path
    # must not leave a private thread nobody owns.
    upsert.reset_mock()
    monkeypatch.setattr(slack_webhook.User, "login_for_email", AsyncMock(return_value=None))
    await slack_webhook._mark_slack_thread_errored("t1", request, None)
    upsert.assert_not_awaited()
