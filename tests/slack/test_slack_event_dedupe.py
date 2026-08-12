import asyncio
import json
from typing import Any, cast

import pytest
from fastapi import BackgroundTasks
from starlette.requests import Request

from agent.utils import slack_events
from agent.webhooks import common as webhook_common
from agent.webhooks import slack as slack_service
from agent.webhooks import slack_routes


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: tuple[str, ...], key: str) -> dict[str, Any] | None:
        return self.items.get((namespace, key))

    async def put_item(self, namespace: tuple[str, ...], key: str, value: dict[str, Any]) -> None:
        self.items[(namespace, key)] = {"value": value}


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()


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

    async def channel_context(_channel_id: str) -> dict[str, Any]:
        return {}

    async def not_docs_plz(_channel_id: str, _context: dict[str, Any]) -> bool:
        return False

    async def repo_config(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"owner": "langchain-ai", "name": "open-swe"}

    monkeypatch.setattr(slack_events, "get_client", lambda url: client)
    monkeypatch.setattr(webhook_common, "verify_slack_signature", lambda **_kwargs: True)
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


async def test_retry_header_alone_does_not_drop_an_unseen_event() -> None:
    background_tasks = _FakeBackgroundTasks()

    response = await _post(_mention_payload("EvNew"), background_tasks, {"X-Slack-Retry-Num": "2"})

    assert response["status"] == "accepted"
    assert len(background_tasks.tasks) == 1


async def test_concurrent_redeliveries_start_one_run() -> None:
    background_tasks = _FakeBackgroundTasks()

    responses = await asyncio.gather(
        *(_post(_mention_payload(), background_tasks) for _ in range(3))
    )

    assert [response["status"] for response in responses].count("accepted") == 1
    assert len(background_tasks.tasks) == 1


async def test_claim_survives_a_restarted_process(_patch_slack_webhook: _FakeClient) -> None:
    background_tasks = _FakeBackgroundTasks()

    await _post(_mention_payload(), background_tasks)
    # A redelivery landing on another instance only has the store to go on.
    slack_events.reset_slack_event_claims()
    second = await _post(_mention_payload(), background_tasks)

    assert second["status"] == "ignored"
    assert len(background_tasks.tasks) == 1
