"""The Slack interactivity handler for DM'd PR approval buttons."""

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

from agent.slack import client as slack_client
from agent.slack import routes as slack_routes
from agent.slack.payloads import SlackChannelContext
from agent.threads import pr_approval
from agent.users import User

_SECRET = "test-signing-secret"


def _request(action: str, fingerprint: str = "fp1", user_id: str = "U-ALICE") -> Request:
    value = {"type": "pr_approval", "action": action, "fingerprint": fingerprint}
    payload = {
        "actions": [
            {
                "action_id": f"open_swe_option_select_pr_approval_{action}",
                "action_ts": "3.0",
                "text": {"type": "plain_text", "text": action},
                "value": json.dumps({**value, "thread_id": "thread-1"}),
            }
        ],
        "channel": {"id": "D-ALICE"},
        "message": {"ts": "2.0", "text": "Approve PR"},
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
def stack(monkeypatch, fake_store):
    monkeypatch.setattr(slack_routes.common, "SLACK_SIGNING_SECRET", _SECRET)
    monkeypatch.setattr(
        slack_routes.common,
        "resolve_slack_channel_context",
        AsyncMock(return_value=SlackChannelContext(id="D-ALICE", is_im=True)),
    )
    monkeypatch.setattr(
        User,
        "login_for_slack",
        AsyncMock(side_effect={"U-ALICE": "alice", "U-BOB": "bob"}.get),
    )
    record = {"fingerprint": "fp1", "status": "pending", "author_login": "alice"}
    monkeypatch.setattr(pr_approval, "get_pr_approvals", AsyncMock(return_value={"fp1": record}))
    decide = AsyncMock(return_value=record)
    monkeypatch.setattr(pr_approval, "decide_pr_approval", decide)
    monkeypatch.setattr(
        slack_client,
        "get_active_slack_thread",
        AsyncMock(return_value={"channel_id": "C1", "thread_ts": "1.0"}),
    )
    thread_reply = AsyncMock()
    monkeypatch.setattr(slack_routes.common, "post_slack_thread_reply", thread_reply)
    ephemeral = AsyncMock()
    monkeypatch.setattr(slack_routes.common, "post_slack_ephemeral_message", ephemeral)
    return SimpleNamespace(decide=decide, thread_reply=thread_reply, ephemeral=ephemeral)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "approved", "always_allow"),
    [("approve", True, False), ("always_allow", True, True), ("reject", False, False)],
)
async def test_author_decision_is_recorded_and_announced_in_the_thread(
    stack, action, approved, always_allow
):
    await slack_routes.slack_interactivity(_request(action), BackgroundTasks())

    kwargs = stack.decide.await_args.kwargs
    assert stack.decide.await_args.args == ("thread-1", "fp1")
    assert (kwargs["approved"], kwargs["always_allow"], kwargs["actor"]) == (
        approved,
        always_allow,
        "alice",
    )
    assert stack.thread_reply.await_args.kwargs["channel_id"] == "C1"


@pytest.mark.asyncio
async def test_only_the_author_can_answer(stack):
    await slack_routes.slack_interactivity(_request("approve", user_id="U-BOB"), BackgroundTasks())

    stack.decide.assert_not_awaited()
    stack.ephemeral.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_request_is_not_decided(stack):
    await slack_routes.slack_interactivity(_request("approve", "gone"), BackgroundTasks())

    stack.decide.assert_not_awaited()
