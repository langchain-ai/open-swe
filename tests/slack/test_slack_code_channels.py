import json
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks
from starlette.requests import Request

from agent.slack import client as slack_utils
from agent.slack import events as slack_events
from agent.slack import routes as slack_routes
from agent.slack import webhook as slack_service
from agent.slack.payloads import SlackChannelContext
from agent.slack.request import SlackRequest
from agent.webhooks import common as webhook_common


class _FakeRequest:
    def __init__(self, payload: dict[str, Any] | bytes) -> None:
        self.headers: dict[str, str] = {}
        self._body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    async def body(self) -> bytes:
        return self._body


@pytest.mark.parametrize("text", ["talking to a teammate", "talking about @openswe"])
def test_untagged_code_channel_message_does_not_interrupt_active_work(
    monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USERNAME", "openswe")
    assert not slack_service._interrupts_active_run(
        text,
        "BOT",
        treat_all_messages_as_mentions=True,
        code_channel=True,
        message_update=False,
        explicit_request=False,
    )
    assert slack_service._interrupts_active_run(
        "<@BOT> stop and do this",
        "BOT",
        treat_all_messages_as_mentions=True,
        code_channel=True,
        message_update=False,
        explicit_request=False,
    )
    assert slack_service._interrupts_active_run(
        "/run-tests",
        "BOT",
        treat_all_messages_as_mentions=True,
        code_channel=True,
        message_update=False,
        explicit_request=True,
    )


async def test_untagged_code_channel_message_routes_to_the_channel_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slack_events.reset_slack_event_claims()
    monkeypatch.setattr("agent.incidents.channels.handle_slack_event", AsyncMock(return_value=None))

    async def channel_context(_channel_id: str, *, use_cache: bool = True) -> SlackChannelContext:
        return SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=False)

    monkeypatch.setattr(webhook_common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(webhook_common, "claim_slack_event", AsyncMock(return_value=True))
    monkeypatch.setattr(webhook_common, "is_code_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(webhook_common, "resolve_slack_thread_id", AsyncMock(return_value="t1"))
    monkeypatch.setattr(webhook_common, "thread_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(webhook_common, "resolve_slack_channel_context", channel_context)
    monkeypatch.setattr(
        webhook_common,
        "get_slack_repo_config",
        AsyncMock(return_value={"owner": "langchain-ai", "name": "open-swe"}),
    )
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USER_ID", "BOT")

    background_tasks = BackgroundTasks()
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
                        "thread_ts": "1786573300.000000",
                        "user": "U1",
                        "text": "no mention needed here",
                    },
                }
            ),
        ),
        background_tasks,
    )

    assert response["status"] == "accepted", response
    request = cast(SlackRequest, background_tasks.tasks[0].args[0])
    assert request.code_channel is True
    assert request.treat_all_messages_as_mentions is True
    assert request.thread_ts == webhook_common.CODE_CHANNEL_SESSION_TS
    assert request.reply_thread_ts == "1786573300.000000"


@pytest.fixture
def invite_call(slack_api) -> dict[str, Any]:
    captured: dict[str, Any] = {"payload": None, "response": {"ok": True}}

    def handle(method, params, headers):
        assert method == "conversations.invite"
        captured["payload"] = params
        return 200, captured["response"], {}

    slack_api.handler = handle
    return captured


async def test_a_stale_id_costs_only_itself(invite_call: dict[str, Any]) -> None:
    invite_call["response"] = {
        "ok": True,
        "errors": [{"user": "U2", "ok": False, "error": "user_not_found"}],
    }

    invited, error = await slack_utils.invite_to_slack_channel("C1", ["U1", "U2", "U3"])

    assert invited == ["U1", "U3"]
    assert error == "U2 (user_not_found)"


async def test_a_refused_call_names_everyone_it_could_not_invite(
    invite_call: dict[str, Any],
) -> None:
    invite_call["response"] = {"ok": False, "error": "missing_scope"}

    assert await slack_utils.invite_to_slack_channel("C1", ["U1", "U2"]) == (
        [],
        "U1, U2: missing_scope",
    )
