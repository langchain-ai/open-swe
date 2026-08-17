"""Route-level cover for the `untagged_reply` flag handed to the agent prompt."""

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


@pytest.fixture(autouse=True)
def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
    slack_events.reset_slack_event_claims()

    async def channel_context(_channel_id: str) -> dict[str, Any]:
        return {}

    async def not_docs_plz(_channel_id: str, _context: dict[str, Any]) -> bool:
        return False

    async def repo_config(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"owner": "langchain-ai", "name": "open-swe"}

    monkeypatch.setattr(slack_events, "get_client", lambda url: _FakeClient())
    monkeypatch.setattr(webhook_common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(webhook_common, "_get_slack_channel_context", channel_context)
    monkeypatch.setattr(webhook_common, "_is_docs_plz_slack_channel", not_docs_plz)
    monkeypatch.setattr(webhook_common, "get_slack_repo_config", repo_config)
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USER_ID", "BOT")
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USERNAME", "openswe")
    # The two-party gate would admit these messages on its own.
    monkeypatch.setattr(
        slack_service, "_slack_thread_allows_untagged_reply", AsyncMock(return_value=True)
    )


async def _untagged_flag_for(text: str, event_id: str) -> bool:
    background_tasks = _FakeBackgroundTasks()
    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_message_payload(text, event_id))),
        cast(BackgroundTasks, background_tasks),
    )
    assert response["status"] == "accepted", response
    event_data = background_tasks.tasks[0][1][0]
    return bool(event_data["untagged_reply"])


async def test_id_mention_is_not_marked_untagged() -> None:
    assert await _untagged_flag_for("<@BOT> please fix this", "Ev-id") is False


async def test_username_mention_is_not_marked_untagged() -> None:
    assert await _untagged_flag_for("hey @openswe please fix this", "Ev-name") is False


async def test_message_without_a_mention_is_marked_untagged() -> None:
    assert await _untagged_flag_for("how about now", "Ev-plain") is True
