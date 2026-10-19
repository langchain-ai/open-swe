import json
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks

from openswe.human_review.offer import GET_REVIEWED
from openswe.slack import events as slack_events
from openswe.slack import suggested_actions
from openswe.slack.payloads import SlackButtonValue, SlackChannelContext, SlackInteraction
from openswe.slack.request import SlackRequest
from openswe.webhooks import common


class _Tasks:
    def __init__(self) -> None:
        self.tasks: list[tuple[Any, tuple[Any, ...]]] = []

    def add_task(self, func: Any, *args: Any) -> None:
        self.tasks.append((func, args))


async def test_accepting_a_suggestion_asks_the_slack_threads_agent_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slack_events.reset_slack_event_claims()
    monkeypatch.setattr(slack_events, "_claim_remotely", AsyncMock(return_value=True))
    respond = AsyncMock(return_value=True)
    monkeypatch.setattr(suggested_actions, "respond_to_slack_interaction", respond)
    monkeypatch.setattr(suggested_actions, "is_kitchen_channel", AsyncMock(return_value=False))
    monkeypatch.setattr(
        common, "resolve_slack_channel_context", AsyncMock(return_value=SlackChannelContext())
    )
    monkeypatch.setattr(common, "resolve_slack_thread_id", AsyncMock(return_value="t1"))
    monkeypatch.setattr(common, "get_slack_repo_config", AsyncMock(return_value=None))
    value = {
        "type": suggested_actions.BUTTON_TYPE,
        "action": "accept",
        "kind": GET_REVIEWED.kind,
        "thread_ts": "100.000001",
        "subject": "https://github.com/lc/repo/pull/7",
    }
    interaction = cast(
        SlackInteraction,
        SlackInteraction.parse(
            {
                "type": "block_actions",
                "response_url": "https://hooks.slack.com/actions/T/1/x",
                "channel": {"id": "C1"},
                "user": {"id": "U1"},
                "container": {"type": "message", "is_ephemeral": True},
                "actions": [{"action_id": "x", "value": json.dumps(value), "action_ts": "200.5"}],
            }
        ),
    )
    button = cast(SlackButtonValue, SlackButtonValue.parse(value))
    tasks = _Tasks()

    for _ in range(2):
        await suggested_actions.handle_button(interaction, button, cast(BackgroundTasks, tasks))

    assert len(tasks.tasks) == 1
    request = cast(SlackRequest, tasks.tasks[0][1][0])
    assert (request.channel_id, request.thread_ts, request.thread_id) == ("C1", "100.000001", "t1")
    assert request.event_ts == "200.5"
    assert request.text == "Get https://github.com/lc/repo/pull/7 reviewed."
    assert "<@U1>" in request.turn_context
    respond.assert_awaited_once()


async def test_asking_whether_to_reply_asks_the_messages_sender_privately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.slack.tools import ask_whether_to_reply as tool

    messages = {
        "1.1": {"ts": "1.1", "user": "U_SENDER", "text": "why'd it tag you?"},
        "1.2": {"ts": "1.2", "bot_id": "B1", "text": "a bot"},
    }

    async def fetch(channel_id: str, thread_ts: str, message_ts: str) -> dict[str, str] | None:
        return messages.get(message_ts)

    post = AsyncMock(return_value=True)
    monkeypatch.setattr(
        tool,
        "get_config",
        lambda: {
            "configurable": {
                "slack_thread": {
                    "channel_id": "C1",
                    "thread_ts": "1.0",
                    "triggering_event_ts": "1.3",
                    "triggering_user_id": "U_OTHER",
                }
            }
        },
    )
    monkeypatch.setattr(tool, "langgraph_client", lambda: None)
    monkeypatch.setattr(
        tool, "get_active_slack_thread", AsyncMock(side_effect=lambda _c, _t, fallback: fallback)
    )
    monkeypatch.setattr(tool, "fetch_slack_thread_message_by_ts", fetch)
    monkeypatch.setattr(suggested_actions, "post_slack_ephemeral_message", post)

    assert (await tool.slack_ask_whether_to_reply("1.2"))["success"] is False
    assert (await tool.slack_ask_whether_to_reply("1.1"))["success"] is True

    post.assert_awaited_once()
    channel_id, user_id, _text, thread_ts = post.await_args.args
    assert (channel_id, user_id, thread_ts) == ("C1", "U_SENDER", "1.0")
    reply = json.loads(post.await_args.kwargs["blocks"][1]["elements"][0]["value"])
    assert (reply["kind"], reply["subject"]) == (suggested_actions.REPLY.kind, "1.1")
