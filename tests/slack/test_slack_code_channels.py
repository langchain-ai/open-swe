"""Code channels: every message is a turn, and replies stay top-level."""

import json
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks
from starlette.requests import Request

from agent.utils import slack as slack_utils
from agent.utils import slack_events
from agent.webhooks import common as webhook_common
from agent.webhooks import slack_routes


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


async def test_untagged_code_channel_message_routes_to_the_channel_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slack_events.reset_slack_event_claims()

    async def channel_context(_channel_id: str) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(webhook_common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(webhook_common, "is_code_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(webhook_common, "resolve_slack_thread_id", AsyncMock(return_value="t1"))
    monkeypatch.setattr(webhook_common, "_thread_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(webhook_common, "_get_slack_channel_context", channel_context)
    monkeypatch.setattr(webhook_common, "_is_docs_plz_slack_channel", AsyncMock(return_value=False))
    monkeypatch.setattr(
        webhook_common,
        "get_slack_repo_config",
        AsyncMock(return_value={"owner": "langchain-ai", "name": "open-swe"}),
    )
    monkeypatch.setattr(webhook_common, "increment_slack_thread_version", AsyncMock(return_value=1))
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USER_ID", "BOT")

    background_tasks = _FakeBackgroundTasks()
    response = await slack_routes.slack_webhook(
        cast(
            Request,
            _FakeRequest(
                {
                    "type": "event_callback",
                    "event_id": "Ev-code-channel",
                    "authorizations": [{"user_id": "BOT"}],
                    "event": {
                        "type": "message",
                        "channel": "C-code",
                        "ts": "1786573369.551099",
                        "user": "U1",
                        "text": "no mention needed here",
                    },
                }
            ),
        ),
        cast(BackgroundTasks, background_tasks),
    )

    assert response["status"] == "accepted", response
    event_data = background_tasks.tasks[0][1][0]
    assert event_data["code_channel"] is True
    assert event_data["treat_all_messages_as_mentions"] is True
    assert event_data["thread_ts"] == webhook_common.CODE_CHANNEL_SESSION_TS


async def test_code_channel_replies_are_posted_top_level(monkeypatch: pytest.MonkeyPatch) -> None:
    posted: dict[str, Any] = {}

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"ok": True, "ts": "1786573400.000000"}

    class _Client:
        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(self, _url: str, **kwargs: Any) -> _Response:
            posted.update(kwargs["json"])
            return _Response()

    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(slack_utils.httpx, "AsyncClient", lambda **_kwargs: _Client())

    await slack_utils._post_slack_message_with_ts(
        "C-code", "done", thread_ts=webhook_common.CODE_CHANNEL_SESSION_TS
    )

    assert "thread_ts" not in posted
