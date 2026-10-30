import asyncio
import json
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks, HTTPException
from starlette.requests import Request

from openswe.slack import events as slack_events
from openswe.slack import failures as slack_failures
from openswe.slack import routes as slack_routes
from openswe.slack.payloads import SlackChannelContext
from openswe.webhooks import common as webhook_common


class _FakeClaims:
    def __init__(self) -> None:
        self.held: set[tuple[str, str]] = set()
        self.lock = asyncio.Lock()

    async def claim(self, scope: str, key: str, **_kwargs: Any) -> bool:
        async with self.lock:
            if (scope, key) in self.held:
                return False
            self.held.add((scope, key))
            return True

    async def release(self, scope: str, key: str) -> None:
        async with self.lock:
            self.held.discard((scope, key))


class _FakeBackgroundTasks:
    def __init__(self) -> None:
        self.tasks: list[tuple[Any, tuple[Any, ...]]] = []

    def add_task(self, func: Any, *args: Any) -> None:
        self.tasks.append((func, args))


class _FakeRequest:
    def __init__(self, payload: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        self.headers: dict[str, str] = headers or {}
        self._body = json.dumps(payload).encode()

    async def body(self) -> bytes:
        return self._body


def _mention_payload(event_id: str = "Ev1") -> dict[str, Any]:
    return {
        "type": "event_callback",
        "event_id": event_id,
        "authorizations": [{"user_id": "BOT"}],
        "event": {
            "type": "app_mention",
            "channel": "C1",
            "ts": "1786573369.551099",
            "user": "U1",
            "text": "<@BOT> hello?",
        },
    }


def _channel_message_payload(event_id: str = "Ev2") -> dict[str, Any]:
    payload = _mention_payload(event_id)
    payload["event"] = {**payload["event"], "type": "message", "channel_type": "channel"}
    return payload


async def _post(
    payload: dict[str, Any],
    background_tasks: _FakeBackgroundTasks,
    headers: dict[str, str] | None = None,
) -> dict[str, str]:
    return await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(payload, headers)),
        cast(BackgroundTasks, background_tasks),
    )


@pytest.fixture(autouse=True)
def _patch_slack_webhook(monkeypatch: pytest.MonkeyPatch) -> _FakeClaims:
    slack_events.reset_slack_event_claims()
    claims = _FakeClaims()
    monkeypatch.setattr(
        "openswe.incidents.channels.handle_slack_event", AsyncMock(return_value=None)
    )

    async def channel_context(_channel_id: str, *, use_cache: bool = True) -> SlackChannelContext:
        return SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=False)

    async def repo_config(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"owner": "langchain-ai", "name": "open-swe"}

    monkeypatch.setattr(slack_events.event_claims, "claim", claims.claim)
    monkeypatch.setattr(slack_events.event_claims, "release", claims.release)
    monkeypatch.setattr(webhook_common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(webhook_common, "resolve_slack_thread_id", AsyncMock(return_value="t1"))
    monkeypatch.setattr(webhook_common, "resolve_slack_channel_context", channel_context)
    monkeypatch.setattr(webhook_common, "get_slack_repo_config", repo_config)
    return claims


async def test_mention_and_message_deliveries_start_one_run(
    monkeypatch: pytest.MonkeyPatch,
    _patch_slack_webhook: _FakeClaims,
) -> None:
    background_tasks = _FakeBackgroundTasks()
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USER_ID", "BOT")

    first = await _post(_mention_payload("Ev1"), background_tasks)
    slack_events.reset_slack_event_claims()
    second = await _post(_channel_message_payload("Ev2"), background_tasks)

    assert first["status"] == "accepted"
    assert second["status"] == "ignored"
    assert len(background_tasks.tasks) == 1
    assert _patch_slack_webhook.held == {
        ("slack_event", "Ev1"),
        ("slack_event", "Ev2"),
        ("slack_event", "C1:1786573369.551099"),
    }


def _bot_payload(*, event_type: str = "message", with_user: bool = True) -> dict[str, Any]:
    payload = _mention_payload()
    payload.update({"team_id": "T123", "api_app_id": "AOWN"})
    payload["event"].update(
        {
            "type": event_type,
            "subtype": "bot_message",
            "bot_id": "B123",
            "app_id": "A123",
            "user": "U123",
        }
    )
    if not with_user:
        payload["event"].pop("user")
    return payload


@pytest.mark.parametrize(
    "change",
    [
        {"bot_id": "BOTHER"},
        {"user": "UOTHER"},
        {"app_id": "AOTHER"},
        {"user": "BOT"},
        {"app_id": "AOWN"},
        {"text": "A status update"},
        {"subtype": "message_deleted"},
    ],
)
async def test_bot_must_be_allowed_and_explicitly_mention_us(
    allowed_bot: None,
    change: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(webhook_common, "is_code_channel", AsyncMock(return_value=True))
    payload = _bot_payload()
    payload["event"].update(change)
    tasks = _FakeBackgroundTasks()
    assert (await _post(payload, tasks))["status"] == "ignored"
    assert tasks.tasks == []


@pytest.mark.parametrize("team_id", ["", "TOTHER"])
async def test_bot_allowlist_is_workspace_scoped(allowed_bot: None, team_id: str) -> None:
    payload = _bot_payload()
    payload["team_id"] = team_id
    tasks = _FakeBackgroundTasks()
    assert (await _post(payload, tasks))["status"] == "ignored"
    assert tasks.tasks == []


async def test_bot_authorization_is_workspace_owned(
    allowed_bot: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "someone-else")
    tasks = _FakeBackgroundTasks()
    assert (await _post(_bot_payload(), tasks))["status"] == "accepted"
    assert len(tasks.tasks) == 1


async def test_retry_header_alone_does_not_drop_an_unseen_event() -> None:
    background_tasks = _FakeBackgroundTasks()

    response = await _post(_mention_payload("EvNew"), background_tasks, {"X-Slack-Retry-Num": "2"})

    assert response["status"] == "accepted"
    assert len(background_tasks.tasks) == 1


async def test_concurrent_cross_instance_redeliveries_start_one_run() -> None:
    background_tasks = _FakeBackgroundTasks()

    async def post() -> dict[str, str]:
        slack_events.reset_slack_event_claims()
        return await _post(_mention_payload(), background_tasks)

    responses = await asyncio.gather(*(post() for _ in range(3)))

    assert [response["status"] for response in responses].count("accepted") == 1
    assert len(background_tasks.tasks) == 1


async def test_external_channel_refuses_without_starting_a_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    background_tasks = _FakeBackgroundTasks()
    post_reply = AsyncMock(return_value=True)
    resolve_thread = cast(AsyncMock, webhook_common.resolve_slack_thread_id)
    monkeypatch.setattr(
        webhook_common,
        "resolve_slack_channel_context",
        AsyncMock(return_value=SlackChannelContext(is_ext_shared=True)),
    )
    monkeypatch.setattr(webhook_common, "post_slack_thread_reply", post_reply)

    response = await _post(_mention_payload(), background_tasks)

    assert response == {"status": "ignored", "reason": "Slack channel is not eligible"}
    assert len(background_tasks.tasks) == 1
    await background_tasks.tasks[0][0](*background_tasks.tasks[0][1])
    post_reply.assert_awaited_once_with(
        "C1",
        "1786573369.551099",
        slack_routes._EXTERNAL_CHANNEL_REFUSAL,
    )
    resolve_thread.assert_not_awaited()


@pytest.mark.parametrize(
    "context",
    [
        SlackChannelContext(lookup_retry_after=60),
        SlackChannelContext(is_ext_shared=False, lookup_retry_after=60),
    ],
)
async def test_unverified_channel_retries_without_reply_or_run(
    monkeypatch: pytest.MonkeyPatch,
    context: SlackChannelContext,
    _patch_slack_webhook: _FakeClaims,
) -> None:
    background_tasks = _FakeBackgroundTasks()
    post_reply = AsyncMock(return_value=True)
    resolve_thread = cast(AsyncMock, webhook_common.resolve_slack_thread_id)
    resolve_context = AsyncMock(return_value=context)
    monkeypatch.setattr(webhook_common, "resolve_slack_channel_context", resolve_context)
    monkeypatch.setattr(webhook_common, "post_slack_thread_reply", post_reply)
    monkeypatch.setattr(slack_routes, "is_kitchen_channel", AsyncMock(return_value=True))
    payload = _channel_message_payload()
    payload["event"]["text"] = "open a feature request"

    with pytest.raises(HTTPException) as error:
        await _post(payload, background_tasks)

    assert error.value.status_code == 503
    assert error.value.headers == {"Retry-After": "60"}
    assert background_tasks.tasks == []
    assert _patch_slack_webhook.held == set()
    post_reply.assert_not_awaited()
    resolve_thread.assert_not_awaited()

    resolve_context.return_value = SlackChannelContext(
        is_ext_shared=False, is_pending_ext_shared=False
    )
    response = await _post(payload, background_tasks, {"X-Slack-Retry-Num": "1"})
    assert response["status"] == "accepted"
    assert len(background_tasks.tasks) == 1


@pytest.mark.parametrize(
    "event",
    [
        {"type": "reaction_added", "reaction": "eyes"},
        {"type": "channel_archive"},
        {"type": "message", "subtype": "bot_message", "bot_id": "B1"},
        {"type": "message", "text": "ordinary channel chatter"},
        {"type": "message", "text": "<@OTHER> can you help?"},
    ],
)
async def test_transient_lookup_does_not_retry_irrelevant_events(
    monkeypatch: pytest.MonkeyPatch,
    event: dict[str, str],
) -> None:
    monkeypatch.setattr(
        webhook_common,
        "resolve_slack_channel_context",
        AsyncMock(return_value=SlackChannelContext(lookup_retry_after=30)),
    )
    monkeypatch.setattr(slack_routes, "is_kitchen_channel", AsyncMock(return_value=False))
    payload = _channel_message_payload()
    payload["event"].update(event)
    tasks = _FakeBackgroundTasks()
    assert (await _post(payload, tasks))["status"] == "ignored"


async def test_transient_lookup_retries_an_allowlisted_bot_mention(
    monkeypatch: pytest.MonkeyPatch,
    allowed_bot: None,
) -> None:
    monkeypatch.setattr(
        webhook_common,
        "resolve_slack_channel_context",
        AsyncMock(return_value=SlackChannelContext(lookup_retry_after=17)),
    )
    with pytest.raises(HTTPException) as error:
        await _post(_bot_payload(), _FakeBackgroundTasks())
    assert error.value.status_code == 503
    assert error.value.headers == {"Retry-After": "17"}


async def test_definitive_lookup_failure_does_not_retry_a_mention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        webhook_common,
        "resolve_slack_channel_context",
        AsyncMock(return_value=SlackChannelContext()),
    )
    tasks = _FakeBackgroundTasks()
    assert (await _post(_mention_payload(), tasks))["status"] == "ignored"
    assert tasks.tasks == []


async def test_pending_external_channel_is_not_retried_or_dispatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    background_tasks = _FakeBackgroundTasks()
    monkeypatch.setattr(
        webhook_common,
        "resolve_slack_channel_context",
        AsyncMock(return_value=SlackChannelContext(is_pending_ext_shared=True)),
    )

    response = await _post(_channel_message_payload(), background_tasks)

    assert response == {"status": "ignored", "reason": "Slack channel is not eligible"}
    assert background_tasks.tasks == []


@pytest.mark.parametrize(
    "event",
    [
        {"type": "message", "subtype": "bot_message", "bot_id": "BOTHER", "text": "status"},
        {"type": "message", "user": "BOT", "text": "my own reply"},
        {"type": "reaction_added", "reaction": "eyes"},
        {"type": "user_change"},
    ],
)
async def test_irrelevant_events_do_not_verify_channels(
    monkeypatch: pytest.MonkeyPatch,
    event: dict[str, str],
) -> None:
    verify = AsyncMock(side_effect=AssertionError("must not contact Slack"))
    monkeypatch.setattr(webhook_common, "resolve_slack_channel_context", verify)
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USER_ID", "BOT")
    payload = _channel_message_payload()
    payload["event"].update(event)
    tasks = _FakeBackgroundTasks()
    assert (await _post(payload, tasks))["status"] == "ignored"
    assert tasks.tasks == []
    verify.assert_not_awaited()


async def test_watched_bot_messages_still_verify_and_launch_automations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_routes, "_slack_channel_watched", AsyncMock(return_value=True))
    payload = _bot_payload()
    payload["event"]["text"] = "deployment status"
    tasks = _FakeBackgroundTasks()
    assert (await _post(payload, tasks))["status"] == "ignored"
    assert len(tasks.tasks) == 1
    assert tasks.tasks[0][0] == slack_routes._launch_slack_automations


async def test_already_handled_delivery_skips_live_channel_check(
    monkeypatch: pytest.MonkeyPatch,
    registry_db: None,
) -> None:
    from datetime import timedelta

    from openswe import event_claims

    tasks = _FakeBackgroundTasks()
    assert (await _post(_mention_payload(), tasks))["status"] == "accepted"
    await event_claims.defer("slack_event", "Ev1", ttl=timedelta(minutes=1))
    slack_events.reset_slack_event_claims()
    verify = AsyncMock(side_effect=AssertionError("duplicate must not contact Slack"))
    monkeypatch.setattr(webhook_common, "resolve_slack_channel_context", verify)
    assert (await _post(_mention_payload(), tasks))["status"] == "ignored"
    assert len(tasks.tasks) == 1
    verify.assert_not_awaited()


async def test_preprocessing_failure_replies_and_does_not_claim_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    background_tasks = _FakeBackgroundTasks()
    post_reply = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_failures, "post_slack_thread_reply", post_reply)

    async def failed_repo_config(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        raise RuntimeError

    original_repo_config = webhook_common.get_slack_repo_config
    monkeypatch.setattr(webhook_common, "get_slack_repo_config", failed_repo_config)
    response = await _post(_mention_payload(), background_tasks)

    assert response["status"] == "error"
    assert background_tasks.tasks == []
    post_reply.assert_awaited_once()
    await_args = post_reply.await_args
    assert await_args is not None
    assert await_args.args[:2] == ("C1", "1786573369.551099")
    assert f"Error ID: `{response['error_id']}`" in await_args.args[2]

    monkeypatch.setattr(webhook_common, "get_slack_repo_config", original_repo_config)
    assert (await _post(_mention_payload(), background_tasks))["status"] == "accepted"
