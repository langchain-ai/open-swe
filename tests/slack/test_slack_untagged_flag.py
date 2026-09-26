"""Route-level coverage for Slack mention requirements and message edits."""

import json
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks
from starlette.requests import Request

from agent.slack import events as slack_events
from agent.slack import routes as slack_routes
from agent.slack import webhook as slack_service
from agent.slack.payloads import SlackChannelContext
from agent.slack.request import SlackRequest
from agent.webhooks import common as webhook_common


class _FakeThreads:
    """Every claim succeeds — dedupe is covered in test_slack_event_dedupe.py."""

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


def _message_payload(text: str, event_id: str) -> dict[str, Any]:
    return {
        "type": "event_callback",
        "event_id": event_id,
        "authorizations": [{"user_id": "BOT"}],
        "event": {
            "type": "message",
            "channel": "C1",
            "ts": "1786573369.551099",
            "thread_ts": "1786573300.000000",
            "user": "U1",
            "text": text,
        },
    }


def _message_update_payload(*, bot_message: bool = False) -> dict[str, Any]:
    updated_message: dict[str, Any] = {
        "type": "message",
        "user": "BOT" if bot_message else "U1",
        "text": "new corrected text",
        "ts": "1786573369.551099",
        "thread_ts": "1786573300.000000",
    }
    if bot_message:
        updated_message["bot_id"] = "B1"
    return {
        "type": "event_callback",
        "event_id": "Ev-update",
        "authorizations": [{"user_id": "BOT"}],
        "event": {
            "type": "message",
            "subtype": "message_changed",
            "channel": "C1",
            "event_ts": "1786573400.000000",
            "ts": "1786573400.000000",
            "message": updated_message,
            "previous_message": {
                "type": "message",
                "user": "BOT" if bot_message else "U1",
                "text": "old text that must not be resent",
                "ts": "1786573369.551099",
                "thread_ts": "1786573300.000000",
            },
        },
    }


@pytest.fixture(autouse=True)
def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
    slack_events.reset_slack_event_claims()
    monkeypatch.setattr(slack_routes, "allow_solo_thread_followup", AsyncMock(return_value=False))
    monkeypatch.setattr("agent.incidents.channels.handle_slack_event", AsyncMock(return_value=None))

    async def channel_context(_channel_id: str, *, use_cache: bool = True) -> SlackChannelContext:
        return SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=False)

    async def repo_config(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"owner": "langchain-ai", "name": "open-swe"}

    monkeypatch.setattr(slack_events, "get_client", lambda url: _FakeClient())
    monkeypatch.setattr(webhook_common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(webhook_common, "resolve_slack_thread_id", AsyncMock(return_value="t1"))
    monkeypatch.setattr(webhook_common, "lookup_slack_thread_id", AsyncMock(return_value="t1"))
    monkeypatch.setattr(
        webhook_common,
        "lookup_slack_run_mapping",
        AsyncMock(
            return_value={
                "run_id": "run-1",
                "thread_ts": "1786573300.000000",
                "triggering_user_id": "U1",
                "agent_thread_id": "t1",
            }
        ),
    )
    monkeypatch.setattr(webhook_common, "thread_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(webhook_common, "resolve_slack_channel_context", channel_context)
    monkeypatch.setattr(webhook_common, "get_slack_repo_config", repo_config)
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USER_ID", "BOT")
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USERNAME", "openswe")


@pytest.mark.parametrize(
    "text",
    ["<@BOT> please fix this", "hey @openswe please fix this"],
)
async def test_tagged_thread_message_is_accepted(text: str) -> None:
    background_tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_message_payload(text, f"Ev-{text}"))),
        cast(BackgroundTasks, background_tasks),
    )

    assert response["status"] == "accepted"
    assert len(background_tasks.tasks) == 1


@pytest.mark.parametrize("subtype", ["", "file_share"])
async def test_untagged_thread_message_is_ignored(subtype: str) -> None:
    payload = _message_payload("the alignment is still wrong", f"Ev-{subtype}")
    payload["event"]["subtype"] = subtype
    background_tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(payload)), cast(BackgroundTasks, background_tasks)
    )

    assert response == {"status": "ignored", "reason": "Not an app mention or DM"}
    assert background_tasks.tasks == []


@pytest.mark.parametrize("reply", [False, True])
async def test_kitchen_messages_start_and_continue_threads_without_tag(
    monkeypatch: pytest.MonkeyPatch, reply: bool
) -> None:
    async def channel_context(_channel_id: str, *, use_cache: bool = True) -> SlackChannelContext:
        return SlackChannelContext(
            name="team-kitchen", is_ext_shared=False, is_pending_ext_shared=False
        )

    monkeypatch.setattr(webhook_common, "resolve_slack_channel_context", channel_context)
    payload = _message_payload("please fix this", f"Ev-kitchen-{reply}")
    if not reply:
        del payload["event"]["thread_ts"]
    background_tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(payload)), cast(BackgroundTasks, background_tasks)
    )

    assert response["status"] == "accepted"
    request = cast(SlackRequest, background_tasks.tasks[0][1][0])
    assert request.thread_ts == ("1786573300.000000" if reply else "1786573369.551099")
    assert request.treat_all_messages_as_mentions is True


async def test_message_update_queues_only_the_new_text() -> None:
    background_tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_message_update_payload())),
        cast(BackgroundTasks, background_tasks),
    )

    assert response["status"] == "accepted"
    assert background_tasks.tasks[0][0] is slack_routes._process_slack_message_update
    request = cast(SlackRequest, background_tasks.tasks[0][1][0])
    assert request.message_update is True
    assert request.event_ts == "1786573400.000000"
    assert request.original_message_ts == "1786573369.551099"
    assert request.thread_ts == "1786573300.000000"
    assert request.text == "new corrected text"
    assert "old text that must not be resent" not in str(request)


async def _run_message_update_task(background_tasks: _FakeBackgroundTasks) -> None:
    func, args = background_tasks.tasks[0]
    await func(*args)


async def test_root_message_update_uses_original_message_as_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _message_update_payload()
    del payload["event"]["message"]["thread_ts"]
    del payload["event"]["previous_message"]["thread_ts"]
    lookup_run = cast(AsyncMock, webhook_common.lookup_slack_run_mapping)
    lookup_run.return_value = {
        "run_id": "run-1",
        "thread_ts": "1786573369.551099",
        "triggering_user_id": "U1",
        "agent_thread_id": "t1",
    }
    background_tasks = _FakeBackgroundTasks()
    process = AsyncMock()
    monkeypatch.setattr(slack_service, "process_slack_mention", process)

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(payload)),
        cast(BackgroundTasks, background_tasks),
    )
    await _run_message_update_task(background_tasks)

    assert response["status"] == "accepted"
    request = cast(SlackRequest, background_tasks.tasks[0][1][0])
    assert request.thread_ts == "1786573369.551099"
    lookup = cast(AsyncMock, webhook_common.lookup_slack_thread_id)
    lookup.assert_awaited_once()
    await_args = lookup.await_args
    assert await_args is not None
    assert await_args.args[2] == "1786573369.551099"
    process.assert_awaited_once()
    process_request = cast(SlackRequest, process.await_args.args[0])
    assert process_request.thread_ts == "1786573369.551099"


@pytest.mark.parametrize(
    ("patch_name", "patch_value"),
    [
        ("lookup_slack_thread_id", None),
        ("thread_exists", False),
        ("lookup_slack_run_mapping", None),
    ],
)
async def test_message_update_background_task_requires_delivered_message(
    monkeypatch: pytest.MonkeyPatch,
    patch_name: str,
    patch_value: object,
) -> None:
    monkeypatch.setattr(webhook_common, patch_name, AsyncMock(return_value=patch_value))
    monkeypatch.setattr(slack_routes, "_MESSAGE_UPDATE_RETRY_DELAYS", ())
    process = AsyncMock()
    monkeypatch.setattr(slack_service, "process_slack_mention", process)
    background_tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_message_update_payload())),
        cast(BackgroundTasks, background_tasks),
    )
    await _run_message_update_task(background_tasks)

    assert response == {"status": "accepted", "message": "Slack update queued"}
    process.assert_not_awaited()


@pytest.mark.parametrize(
    ("field", "value"),
    [("triggering_user_id", "UOTHER"), ("agent_thread_id", "other-thread")],
)
async def test_message_update_background_task_rejects_mismatched_delivery_mapping(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    lookup_run = cast(AsyncMock, webhook_common.lookup_slack_run_mapping)
    delivered = dict(lookup_run.return_value)
    delivered[field] = value
    lookup_run.return_value = delivered
    process = AsyncMock()
    monkeypatch.setattr(slack_service, "process_slack_mention", process)
    background_tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_message_update_payload())),
        cast(BackgroundTasks, background_tasks),
    )
    await _run_message_update_task(background_tasks)

    assert response == {"status": "accepted", "message": "Slack update queued"}
    process.assert_not_awaited()


async def test_message_update_retries_until_delivery_mapping_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lookup_run = AsyncMock(
        side_effect=[
            None,
            {
                "run_id": "run-1",
                "thread_ts": "1786573300.000000",
                "triggering_user_id": "U1",
                "agent_thread_id": "t1",
            },
        ]
    )
    sleep = AsyncMock()
    process = AsyncMock()
    monkeypatch.setattr(webhook_common, "lookup_slack_run_mapping", lookup_run)
    monkeypatch.setattr(slack_routes, "_MESSAGE_UPDATE_RETRY_DELAYS", (0.1,))
    monkeypatch.setattr(slack_routes.asyncio, "sleep", sleep)
    monkeypatch.setattr(slack_service, "process_slack_mention", process)
    background_tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_message_update_payload())),
        cast(BackgroundTasks, background_tasks),
    )
    assert response["status"] == "accepted"
    sleep.assert_not_awaited()

    await _run_message_update_task(background_tasks)

    sleep.assert_awaited_once_with(0.1)
    process.assert_awaited_once()


async def test_message_update_rejects_changed_sender_identity() -> None:
    payload = _message_update_payload()
    payload["event"]["previous_message"]["user"] = "UOTHER"
    background_tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(payload)),
        cast(BackgroundTasks, background_tasks),
    )

    assert response == {"status": "ignored", "reason": "Updated message identity changed"}
    assert background_tasks.tasks == []


async def test_message_update_from_a_bot_is_ignored() -> None:
    background_tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_message_update_payload(bot_message=True))),
        cast(BackgroundTasks, background_tasks),
    )

    assert response == {"status": "ignored", "reason": "Event from a bot"}
    assert background_tasks.tasks == []


async def test_message_update_ignores_link_unfurl_attachments() -> None:
    """Slack unfurls a link by editing the message to add `attachments`.

    Stands in for every metadata-only edit: the text the user wrote is
    unchanged, so there is nothing new to act on whatever else moved.
    """
    payload = _message_update_payload()
    payload["event"]["previous_message"]["text"] = "new corrected text"
    payload["event"]["message"]["attachments"] = [
        {
            "service_name": "GitHub",
            "title": "Fix the thing by someone · Pull Request #5888",
            "title_link": "https://github.com/langchain-ai/deepagents/pull/5888",
        }
    ]
    background_tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(payload)),
        cast(BackgroundTasks, background_tasks),
    )

    assert response == {"status": "ignored", "reason": "No user-visible message changes"}
    assert background_tasks.tasks == []
