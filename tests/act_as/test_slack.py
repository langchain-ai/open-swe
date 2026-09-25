"""Clicks on the act-as DM card."""

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from fastapi import BackgroundTasks, Request

from agent.act_as import slack as act_as_slack
from agent.act_as.records import ThreadActAs
from agent.slack import routes as slack_routes
from agent.slack.payloads import SlackChannelContext
from agent.users import User, UserPreferences, UserPreferencesPatch
from agent.utils.json_types import JsonObject

_SECRET = "test-signing-secret"


def _request(action: str, fingerprint: str, user_id: str = "U-ALICE") -> Request:
    value = {
        "type": act_as_slack.BUTTON_TYPE,
        "action": action,
        "fingerprint": fingerprint,
        "thread_id": "thread-1",
    }
    payload = {
        "actions": [
            {
                "action_id": f"open_swe_option_select_act_as_{action}",
                "action_ts": "3.0",
                "text": {"type": "plain_text", "text": action},
                "value": json.dumps(value),
            }
        ],
        "channel": {"id": "D-ALICE"},
        "message": {"ts": "2.0", "text": "Open SWE wants to open a PR as you"},
        "user": {"id": user_id},
    }
    body = urlencode({"payload": json.dumps(payload)}).encode()
    timestamp = str(int(time.time()))
    digest = hmac.new(_SECRET.encode(), f"v0:{timestamp}:{body.decode()}".encode(), hashlib.sha256)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/webhooks/slack/interactivity",
            "headers": [
                (b"x-slack-request-timestamp", timestamp.encode()),
                (b"x-slack-signature", f"v0={digest.hexdigest()}".encode()),
            ],
        },
        receive,
    )


@pytest.fixture
async def stack(thread_metadata: JsonObject, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setattr(slack_routes.common, "SLACK_SIGNING_SECRET", _SECRET)
    monkeypatch.setattr(
        slack_routes.common,
        "resolve_slack_channel_context",
        AsyncMock(return_value=SlackChannelContext(id="D-ALICE", is_im=True)),
    )
    monkeypatch.setattr(
        User, "login_for_slack", AsyncMock(side_effect={"U-ALICE": "alice", "U-BOB": "bob"}.get)
    )
    preferences = AsyncMock(return_value=UserPreferences(act_as_always_allowed=True))
    monkeypatch.setattr(User, "update_preferences", preferences)
    monkeypatch.setattr(
        act_as_slack,
        "get_active_slack_thread",
        AsyncMock(return_value={"channel_id": "C1", "thread_ts": "1.0"}),
    )
    thread_reply = AsyncMock()
    monkeypatch.setattr(act_as_slack, "post_slack_thread_reply", thread_reply)
    ephemeral = AsyncMock()
    monkeypatch.setattr(act_as_slack, "post_slack_ephemeral_message", ephemeral)
    request = await (await ThreadActAs.load("thread-1")).request(
        "alice", owner="o", repo="r", head="h", base="b", title="t"
    )
    return SimpleNamespace(
        fingerprint=request.fingerprint,
        preferences=preferences,
        thread_reply=thread_reply,
        ephemeral=ephemeral,
    )


async def _status() -> str | None:
    request = (await ThreadActAs.load("thread-1")).for_login("alice")
    return request.status if request is not None else None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "status", "always_allow"),
    [("approve", "approved", False), ("always_allow", "approved", True), ("deny", "denied", False)],
)
async def test_the_persons_answer_is_recorded_and_announced(stack, action, status, always_allow):
    await slack_routes.slack_interactivity(_request(action, stack.fingerprint), BackgroundTasks())

    assert await _status() == status
    assert stack.thread_reply.await_args.args[:2] == ("C1", "1.0")
    if always_allow:
        stack.preferences.assert_awaited_once_with(
            "alice", UserPreferencesPatch(act_as_always_allowed=True)
        )
    else:
        stack.preferences.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_that_person_can_answer(stack):
    await slack_routes.slack_interactivity(
        _request("approve", stack.fingerprint, user_id="U-BOB"), BackgroundTasks()
    )

    assert await _status() == "pending"
    stack.ephemeral.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_request_is_not_decided(stack):
    await slack_routes.slack_interactivity(_request("approve", "gone"), BackgroundTasks())

    assert await _status() == "pending"
    stack.ephemeral.assert_awaited_once()
