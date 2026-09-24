import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks, Request

from agent.input_messages import channel_introduction
from agent.slack import pull_request_watch
from agent.slack import routes as slack_routes
from agent.slack import webhook as slack_webhook
from agent.slack.channel_config import SlackChannelConfig
from agent.slack.payloads import SlackChannelContext
from agent.slack.pull_request_watch import WatchedPullRequest

PR_URL = "https://github.com/Acme/Billing/pull/42"


def _request(event: dict[str, Any]) -> Request:
    body = json.dumps(
        {
            "type": "event_callback",
            "event_id": "Ev1",
            "authorizations": [{"user_id": "U-BOT"}],
            "event": event,
        }
    ).encode()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {"type": "http", "method": "POST", "path": "/webhooks/slack", "headers": []}, receive
    )


def _message(**overrides: Any) -> dict[str, Any]:
    return {
        "type": "message",
        "channel": "C-reviews",
        "user": "U1",
        "text": f"please review <{PR_URL}|billing#42>",
        "ts": "1717171717.000100",
        "event_ts": "1717171717.000100",
        **overrides,
    }


@pytest.fixture
def webhook(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    monkeypatch.setattr("agent.incidents.channels.handle_slack_event", AsyncMock(return_value=None))
    monkeypatch.setattr(SlackChannelContext, "allows_operations", property(lambda _self: True))
    for name, value in {
        "verify_slack_signature": lambda **_: True,
        "resolve_slack_channel_context": AsyncMock(
            return_value=SlackChannelContext(
                id="C-reviews", is_ext_shared=False, is_pending_ext_shared=False
            )
        ),
        "is_code_channel": AsyncMock(return_value=False),
        "slack_event_already_seen": AsyncMock(return_value=False),
        "SLACK_BOT_USER_ID": "U-BOT",
    }.items():
        monkeypatch.setattr(slack_routes.common, name, value)
    monkeypatch.setattr(slack_routes, "allow_solo_thread_followup", AsyncMock(return_value=False))
    record = AsyncMock()
    stop = AsyncMock()
    monkeypatch.setattr(WatchedPullRequest, "record", record)
    monkeypatch.setattr(slack_routes.common, "process_slack_stop_reaction", stop)
    return {"record": record, "stop": stop}


async def _post(event: dict[str, Any]) -> None:
    tasks = BackgroundTasks()
    await slack_routes.slack_webhook(_request(event), tasks)
    for task in tasks.tasks:
        await task()


def test_links_are_deduplicated_across_slack_markup_and_case() -> None:
    text = (
        f"<{PR_URL}|billing#42> and {PR_URL.lower()}/files, "
        "plus <https://github.com/acme/api/pull/7> "
        "but not https://github.com/acme/api/issues/8"
    )

    watches = WatchedPullRequest.linked_in(text, channel_id="C1", message_ts="1.0")

    assert [(w.owner, w.repo, w.number) for w in watches] == [
        ("acme", "api", 7),
        ("acme", "billing", 42),
    ]


async def test_a_top_level_message_with_a_pull_request_is_recorded(
    webhook: dict[str, AsyncMock],
) -> None:
    await _post(_message())

    webhook["record"].assert_awaited_once()
    (watches,) = webhook["record"].await_args.args
    assert [(w.channel_id, w.message_ts, w.number) for w in watches] == [
        ("C-reviews", "1717171717.000100", 42)
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"thread_ts": "1717171700.000100"},
        {"user": "U-BOT"},
        {"text": "no links here"},
    ],
)
async def test_replies_own_messages_and_plain_text_are_not_recorded(
    webhook: dict[str, AsyncMock], overrides: dict[str, str]
) -> None:
    await _post(_message(**overrides))

    webhook["record"].assert_not_awaited()


def _reaction(user: str) -> dict[str, Any]:
    return {
        "type": "reaction_added",
        "user": user,
        "reaction": "x",
        "item": {"type": "message", "channel": "C-reviews", "ts": "1717171717.000100"},
        "event_ts": "1717171800.000100",
    }


async def test_the_bots_own_x_does_not_stop_a_run(webhook: dict[str, AsyncMock]) -> None:
    await _post(_reaction("U-BOT"))

    webhook["stop"].assert_not_awaited()


async def test_a_users_x_still_stops_a_run(webhook: dict[str, AsyncMock]) -> None:
    await _post(_reaction("U1"))

    webhook["stop"].assert_awaited_once()


def _closed(*, merged: bool) -> dict[str, Any]:
    return {
        "action": "closed",
        "repository": {"name": "Billing", "owner": {"login": "Acme"}},
        "pull_request": {"number": 42, "merged": merged},
    }


@pytest.fixture
def closing(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    watches = [
        WatchedPullRequest(
            owner="acme", repo="billing", number=42, channel_id=channel, message_ts="1.0"
        )
        for channel in ("C-on", "C-off")
    ]
    release = AsyncMock(return_value=watches)
    react = AsyncMock(return_value=True)
    monkeypatch.setattr(pull_request_watch.postgres, "configured", lambda: True)
    monkeypatch.setattr(WatchedPullRequest, "release", release)
    monkeypatch.setattr(
        pull_request_watch.SlackChannelConfig,
        "watches_pull_requests",
        AsyncMock(side_effect=lambda channel_id: channel_id == "C-on"),
    )
    monkeypatch.setattr(pull_request_watch, "add_slack_reaction", react)
    return {"release": release, "react": react}


@pytest.mark.parametrize(("merged", "reaction"), [(True, "merged"), (False, "x")])
async def test_close_reacts_only_in_channels_still_watching(
    closing: dict[str, AsyncMock], merged: bool, reaction: str
) -> None:
    await WatchedPullRequest.react_to_close(_closed(merged=merged))

    closing["release"].assert_awaited_once_with("Acme", "Billing", 42)
    closing["react"].assert_awaited_once_with("C-on", "1.0", reaction)


async def test_channel_instructions_reach_the_channel_context_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_webhook, "get_langsmith_trace_url", AsyncMock(return_value=None))
    monkeypatch.setattr(slack_webhook.common, "dashboard_thread_url", lambda _thread_id: "")
    monkeypatch.setattr(
        SlackChannelConfig,
        "instructions_for",
        AsyncMock(return_value="Keep replies short.\nLink the runbook."),
    )

    channel = await slack_webhook._slack_channel_identity(
        "C-reviews",
        "1.0",
        SlackChannelContext(id="C-reviews", name="reviews"),
        thread_id="thread-1",
        repo=None,
    )

    assert channel_introduction(channel)["content"].endswith(
        "standing_instructions:\n  Keep replies short.\n  Link the runbook.\n</dynamic-context>"
    )
