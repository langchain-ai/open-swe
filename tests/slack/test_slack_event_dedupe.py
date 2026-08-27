import asyncio
import json
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks
from starlette.requests import Request

from agent.utils import slack_events
from agent.webhooks import common as webhook_common
from agent.webhooks import slack as slack_service
from agent.webhooks import slack_routes


class _ConflictError(Exception):
    pass


class _FakeThreads:
    def __init__(self) -> None:
        self.ids: set[str] = set()
        self.lock = asyncio.Lock()

    async def create(self, *, thread_id: str, **_kwargs: Any) -> None:
        async with self.lock:
            if thread_id in self.ids:
                raise _ConflictError
            self.ids.add(thread_id)

    async def get(self, thread_id: str) -> dict[str, str]:
        if thread_id not in self.ids:
            raise KeyError(thread_id)
        return {"thread_id": thread_id}


class _FakeClient:
    def __init__(self) -> None:
        self.threads = _FakeThreads()


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
def _patch_slack_webhook(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    slack_events.reset_slack_event_claims()
    client = _FakeClient()

    async def channel_context(_channel_id: str, *, use_cache: bool = True) -> dict[str, Any]:
        return {"is_ext_shared": False, "is_pending_ext_shared": False}

    async def not_docs_plz(_channel_id: str, _context: dict[str, Any]) -> bool:
        return False

    async def repo_config(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"owner": "langchain-ai", "name": "open-swe"}

    monkeypatch.setattr(slack_events, "get_client", lambda url: client)
    monkeypatch.setattr(webhook_common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(webhook_common, "resolve_slack_thread_id", AsyncMock(return_value="t1"))
    monkeypatch.setattr(webhook_common, "_get_slack_channel_context", channel_context)
    monkeypatch.setattr(webhook_common, "_is_docs_plz_slack_channel", not_docs_plz)

    monkeypatch.setattr(webhook_common, "get_slack_repo_config", repo_config)
    return client


async def test_redelivered_event_starts_only_one_run() -> None:
    background_tasks = _FakeBackgroundTasks()

    first = await _post(_mention_payload(), background_tasks)
    second = await _post(_mention_payload(), background_tasks, {"X-Slack-Retry-Num": "1"})

    assert first["status"] == "accepted"
    assert second["status"] == "ignored"
    assert [task[0] for task in background_tasks.tasks] == [slack_service.process_slack_mention]


async def test_redelivered_event_without_retry_header_is_deduped() -> None:
    background_tasks = _FakeBackgroundTasks()

    await _post(_mention_payload(), background_tasks)
    second = await _post(_mention_payload(), background_tasks)

    assert second["status"] == "ignored"
    assert len(background_tasks.tasks) == 1


async def test_mention_and_message_deliveries_start_one_run(
    monkeypatch: pytest.MonkeyPatch,
    _patch_slack_webhook: _FakeClient,
) -> None:
    background_tasks = _FakeBackgroundTasks()
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USER_ID", "BOT")

    first = await _post(_mention_payload("Ev1"), background_tasks)
    slack_events.reset_slack_event_claims()
    second = await _post(_channel_message_payload("Ev2"), background_tasks)

    assert first["status"] == "accepted"
    assert second["status"] == "ignored"
    assert len(background_tasks.tasks) == 1
    assert _patch_slack_webhook.threads.ids == {
        slack_events._claim_thread_id("Ev1"),
        slack_events._claim_thread_id("Ev2"),
        slack_events._claim_thread_id("C1:1786573369.551099"),
    }


async def test_distinct_messages_in_one_channel_both_run() -> None:
    background_tasks = _FakeBackgroundTasks()

    second_message = _mention_payload("Ev2")
    second_message["event"] = {**second_message["event"], "ts": "1786573999.111222"}

    first = await _post(_mention_payload("Ev1"), background_tasks)
    second = await _post(second_message, background_tasks)

    assert [first["status"], second["status"]] == ["accepted", "accepted"]
    assert len(background_tasks.tasks) == 2


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
        "_get_slack_channel_context",
        AsyncMock(return_value={"is_ext_shared": True}),
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


async def test_unverified_channel_fails_closed_without_reply_or_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    background_tasks = _FakeBackgroundTasks()
    post_reply = AsyncMock(return_value=True)
    resolve_thread = cast(AsyncMock, webhook_common.resolve_slack_thread_id)
    monkeypatch.setattr(
        webhook_common,
        "_get_slack_channel_context",
        AsyncMock(return_value={"is_ext_shared": None}),
    )
    monkeypatch.setattr(webhook_common, "post_slack_thread_reply", post_reply)

    response = await _post(_mention_payload(), background_tasks)

    assert response == {"status": "ignored", "reason": "Slack channel is not eligible"}
    assert background_tasks.tasks == []
    post_reply.assert_not_awaited()
    resolve_thread.assert_not_awaited()


async def test_preprocessing_failure_does_not_claim_event(monkeypatch: pytest.MonkeyPatch) -> None:
    background_tasks = _FakeBackgroundTasks()

    async def failed_repo_config(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        raise RuntimeError

    original_repo_config = webhook_common.get_slack_repo_config
    monkeypatch.setattr(webhook_common, "get_slack_repo_config", failed_repo_config)
    with pytest.raises(RuntimeError):
        await _post(_mention_payload(), background_tasks)

    monkeypatch.setattr(webhook_common, "get_slack_repo_config", original_repo_config)
    assert (await _post(_mention_payload(), background_tasks))["status"] == "accepted"
