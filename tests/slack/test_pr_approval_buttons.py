"""The Slack interactivity handler for PR approval buttons."""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks, Request

from agent.slack import routes as slack_routes

_SIGNING_SECRET = "test-signing-secret"


def _signed_request(payload: dict[str, Any]) -> Request:
    """A request whose Slack signature verifies against ``_SIGNING_SECRET``."""
    import hashlib
    import hmac
    import time
    from urllib.parse import urlencode

    body = urlencode({"payload": json.dumps(payload)}).encode()
    timestamp = str(int(time.time()))
    base_string = f"v0:{timestamp}:{body.decode('utf-8', errors='replace')}"
    signature = (
        "v0=" + hmac.new(_SIGNING_SECRET.encode(), base_string.encode(), hashlib.sha256).hexdigest()
    )

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/webhooks/slack/interactivity",
            "headers": [
                (b"x-slack-request-timestamp", timestamp.encode()),
                (b"x-slack-signature", signature.encode()),
            ],
        },
        receive,
    )


def _request(payload: dict[str, Any]) -> Request:
    body = (
        __import__("urllib.parse", fromlist=["urlencode"])
        .urlencode({"payload": json.dumps(payload)})
        .encode()
    )

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/webhooks/slack/interactivity",
            "headers": [],
        },
        receive,
    )


def _pr_approval_payload(action: str, fingerprint: str = "fp1") -> dict[str, Any]:
    action_block = {
        "action_id": f"open_swe_option_select_pr_approval_{action}",
        "action_ts": "3.0",
        "text": {"type": "plain_text", "text": action},
        "value": json.dumps({"type": "pr_approval", "action": action, "fingerprint": fingerprint}),
    }
    return {
        "actions": [action_block],
        "channel": {"id": "C1"},
        "message": {"ts": "2.0", "thread_ts": "1.0", "text": "Approve PR"},
        "user": {"id": "U-ALICE"},
    }


@pytest.fixture
def approval_stack(monkeypatch, fake_store):
    """Stub thread lookup, decision recording, Slack replies, and dispatch."""
    metadata: dict[str, Any] = {}
    update = AsyncMock()
    client = SimpleNamespace(
        threads=SimpleNamespace(
            get=AsyncMock(side_effect=lambda thread_id: {"metadata": dict(metadata)}),
            update=update,
        ),
        store=SimpleNamespace(
            get_item=AsyncMock(return_value={"value": {"thread_id": "thread-1"}}),
        ),
    )
    # lookup_slack_thread_id validates the location shape before the store read.
    monkeypatch.setattr(
        slack_routes.common,
        "lookup_slack_thread_id",
        AsyncMock(return_value="thread-1"),
    )
    monkeypatch.setattr("agent.utils.thread_ops.langgraph_client", lambda **_: client)
    from agent.threads import pr_approval_callback

    decide = AsyncMock(
        side_effect=lambda thread_id, fingerprint, **kwargs: {
            "fingerprint": fingerprint,
            "status": "approved" if kwargs.get("approved") else "rejected",
            "author_login": "alice",
            "requester_login": "bob",
            "decided_by": kwargs.get("actor"),
        }
    )
    monkeypatch.setattr("agent.threads.pr_approval.decide_pr_approval", decide)
    reply = AsyncMock()
    monkeypatch.setattr(slack_routes.common, "post_slack_thread_reply", reply)
    monkeypatch.setattr(
        slack_routes.common,
        "resolve_slack_channel_context",
        AsyncMock(
            return_value=__import__(
                "agent.slack.payloads", fromlist=["SlackChannelContext"]
            ).SlackChannelContext(id="C1", is_im=True)
        ),
    )
    dispatch = AsyncMock()
    monkeypatch.setattr(pr_approval_callback, "dispatch_agent_run", dispatch)
    return SimpleNamespace(
        decide=decide,
        reply=reply,
        dispatch=dispatch,
        update=update,
        metadata=metadata,
    )


@pytest.fixture
def unsigned(monkeypatch):
    monkeypatch.setattr(slack_routes.common, "SLACK_SIGNING_SECRET", _SIGNING_SECRET)


@pytest.mark.asyncio
async def test_approve_button_decides_then_interrupts(approval_stack, unsigned):
    await slack_routes.slack_interactivity(
        _signed_request(_pr_approval_payload("approve")), BackgroundTasks()
    )

    approval_stack.decide.assert_awaited_once()
    args = approval_stack.decide.await_args
    assert args.args[1] == "fp1"
    assert args.kwargs["approved"] is True
    assert args.kwargs["actor"] == "U-ALICE"
    approval_stack.dispatch.assert_awaited_once()
    assert approval_stack.dispatch.await_args.args[0] == "thread-1"
    assert approval_stack.dispatch.await_args.kwargs["multitask_strategy"] == "interrupt"


@pytest.mark.asyncio
async def test_reject_button_interrupts_with_denial(approval_stack, unsigned):
    await slack_routes.slack_interactivity(
        _signed_request(_pr_approval_payload("reject")), BackgroundTasks()
    )

    assert approval_stack.decide.await_args.kwargs["approved"] is False
    approval_stack.dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_always_allow_button_marks_the_preference(approval_stack, unsigned):
    await slack_routes.slack_interactivity(
        _signed_request(_pr_approval_payload("always_allow")), BackgroundTasks()
    )

    assert approval_stack.decide.await_args.kwargs["approved"] is True
    assert approval_stack.decide.await_args.kwargs["always_allow"] is True


@pytest.mark.asyncio
async def test_unknown_fingerprint_replies_without_dispatching(approval_stack, unsigned):
    approval_stack.decide.side_effect = AsyncMock(return_value=None)
    await slack_routes.slack_interactivity(
        _signed_request(_pr_approval_payload("approve", fingerprint="gone")), BackgroundTasks()
    )

    approval_stack.reply.assert_awaited_once()
    approval_stack.dispatch.assert_not_awaited()
