from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openswe.slack import ask as slack_ask
from openswe.slack import routes as slack_routes
from openswe.slack.channels import SlackChannel
from openswe.slack.payloads import SlackChannelContext, SlackMessage
from openswe.slack.tools import reply as slack_reply
from openswe.tasks import schemas as task_schemas
from openswe.tasks import service as task_service
from openswe.threads.listing import _metadata_matches_filters
from openswe.users import User
from openswe.users.models import UserIdentity


@pytest.fixture
def signed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_routes.common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(slack_routes.common, "claim_slack_event", AsyncMock(return_value=True))


def _patch_channel(monkeypatch: pytest.MonkeyPatch, messages: list[SlackMessage]) -> None:
    """Point the channel read at ``messages`` without touching Slack."""
    monkeypatch.setattr(SlackChannel, "load", AsyncMock(return_value=SlackChannel(id="C1")))
    monkeypatch.setattr(SlackChannel, "messages", AsyncMock(return_value=messages))


@pytest.fixture
def linked_asker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        slack_ask.common,
        "resolve_slack_channel_context",
        AsyncMock(return_value=SlackChannelContext(name="eng")),
    )
    monkeypatch.setattr(SlackChannelContext, "allows_operations", property(lambda _self: True))
    monkeypatch.setattr(slack_ask, "get_slack_user_info", AsyncMock(return_value=None))
    monkeypatch.setattr(slack_ask.User, "login_for_slack", AsyncMock(return_value="octocat"))
    monkeypatch.setattr(slack_ask.common, "get_valid_access_token", AsyncMock(return_value="gho_x"))
    _patch_channel(monkeypatch, [])
    monkeypatch.setattr(slack_ask, "get_slack_user_names", AsyncMock(return_value={}))


@pytest.mark.asyncio
@pytest.mark.usefixtures("linked_asker")
@pytest.mark.parametrize(
    "reply_thread_ts,selected_message,newer_reply",
    [
        ("", "", False),
        ("123.456", "", False),
        ("123.456", "", True),
        ("", "Ship the migration", False),
    ],
)
async def test_command_thread_stays_private_and_can_reply_after_task_wakeup(
    monkeypatch: pytest.MonkeyPatch, reply_thread_ts: str, selected_message: str, newer_reply: bool
) -> None:
    status = AsyncMock()
    monkeypatch.setattr("openswe.slack.thinking.set_slack_thread_status", status)
    working = AsyncMock(return_value="123.800")
    monkeypatch.setattr(slack_ask, "post_slack_thread_reply_with_ts", working)
    monkeypatch.setattr(slack_ask, "acknowledge_slack_command", AsyncMock())
    upsert = AsyncMock(return_value=True)
    dispatch = AsyncMock()
    monkeypatch.setattr(
        slack_ask.common,
        "get_slack_repo_config",
        AsyncMock(return_value=slack_ask.common.SlackRepoResolution()),
    )
    monkeypatch.setattr(slack_ask.common, "upsert_agent_thread_metadata", upsert)
    monkeypatch.setattr(slack_ask, "dispatch_agent_run", dispatch)

    request = slack_ask.SlackAskRequest(
        channel_id="C1",
        user_id="U1",
        question="why?",
        selected_message=selected_message,
        thread_id="t-1",
        team_id="T1",
        reply_thread_ts=reply_thread_ts,
        message_ts="123.789" if reply_thread_ts else "",
        response_url="" if reply_thread_ts else "https://hooks.slack.com/commands/T1/1/x",
    )
    await slack_ask.process_slack_ask(request)

    assert upsert.await_args is not None
    assert dispatch.await_args is not None
    assert upsert.await_args.kwargs["unlisted"] is True
    assert upsert.await_args.kwargs["visibility"] == "private"
    assert upsert.await_args.kwargs["owner_login"] == "octocat"
    configurable = dispatch.await_args.args[2]
    assert configurable["slack_ask"] is True
    assert configurable["slack_thread"]["triggering_user_id"] == "U1"
    assert "thread_ts" not in configurable["slack_thread"]
    status.assert_not_awaited()

    client = AsyncMock()
    saved = upsert.await_args.kwargs
    client.threads.get.return_value = {
        "metadata": {
            "owner_type": "user",
            "owner_login": saved["owner_login"],
            "visibility": saved["visibility"],
            "source": saved["source"],
            "source_context": saved["source_context"].dump(),
        }
    }
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "octocat")
    owner = User(identities=[UserIdentity(provider="github", external_id="1", login="octocat")])
    monkeypatch.setattr(task_schemas, "user_for_login", AsyncMock(return_value=owner))
    monkeypatch.setattr(task_service, "langgraph_client", lambda: client)
    monkeypatch.setattr(task_service, "get_profile", AsyncMock(return_value={}))
    monkeypatch.setattr(task_service, "resolve_run_email", AsyncMock(return_value=None))
    resumed = await task_service.recipient_config(request.thread_id)

    from openswe.run_config import RunConfig

    assert RunConfig.parse(resumed).slack_context
    monkeypatch.setattr(slack_reply, "get_config", lambda: {"configurable": resumed})
    monkeypatch.setattr(slack_reply, "get_langgraph_client", lambda: client)
    monkeypatch.setattr(slack_reply.event_claims, "claim", AsyncMock(return_value=True))
    monkeypatch.setattr(slack_reply, "claim_slack_event", AsyncMock(return_value=True))
    monkeypatch.setattr(slack_reply, "settle_slack_thread_status", AsyncMock())
    replace = AsyncMock(return_value=True)
    post = AsyncMock(return_value="123.999")
    update = AsyncMock()
    slack = AsyncMock()
    slack.conversations_replies.return_value = {
        "messages": [{"ts": "123.900" if newer_reply else reply_thread_ts}]
    }

    @asynccontextmanager
    async def bot() -> AsyncIterator[AsyncMock]:
        yield slack

    monkeypatch.setattr(slack_reply.SlackClient, "bot", bot)
    monkeypatch.setattr(slack_reply, "replace_slack_command_message", replace)
    monkeypatch.setattr(slack_reply, "post_slack_thread_reply_with_ts", post)
    monkeypatch.setattr(slack_reply, "update_slack_message", update)

    assert await slack_reply.slack_reply("the worker's answer", "final") == {"success": True}
    if reply_thread_ts:
        working.assert_awaited_once_with(
            "C1", reply_thread_ts, ":hourglass_flowing_sand: Working.."
        )
        if newer_reply:
            assert update.await_args is not None
            assert update.await_args.args == ("C1", "123.800", "the worker's answer")
            assert update.await_args.kwargs["blocks"] == [
                {"type": "markdown", "text": "the worker's answer"}
            ]
            slack.chat_delete.assert_not_awaited()
            post.assert_not_awaited()
        else:
            assert post.await_args is not None
            assert post.await_args.args == ("C1", reply_thread_ts, "the worker's answer")
            slack.chat_delete.assert_awaited_once_with(channel="C1", ts="123.800")
            update.assert_not_awaited()
        replace.assert_not_awaited()
    else:
        assert replace.await_args is not None
        assert replace.await_args.args == (request.response_url, "the worker's answer")
        post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.usefixtures("linked_asker")
async def test_channel_context_stays_inside_its_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_channel(
        monkeypatch,
        [SlackMessage(ts=f"{index}.000000", user="U2", text="x" * 4000) for index in range(1, 30)],
    )
    monkeypatch.setattr(slack_ask, "get_slack_user_names", AsyncMock(return_value={}))

    context = await slack_ask._channel_context("C1")

    assert len(context) <= slack_ask._CHANNEL_CONTEXT_MAX_CHARS
    assert context.startswith(slack_ask._CHANNEL_CONTEXT_TRIMMED)
    assert "29.000000" in context


def test_unlisted_threads_stay_out_of_the_thread_list() -> None:
    filters: dict[str, Any] = {"resolved": None, "source": None, "query": None}

    assert _metadata_matches_filters({"source": "slack"}, **filters)
    assert not _metadata_matches_filters({"source": "slack", "unlisted": True}, **filters)


@pytest.mark.asyncio
async def test_ask_mode_reply_is_ephemeral(monkeypatch: pytest.MonkeyPatch) -> None:
    post = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_reply, "post_slack_ephemeral_reply", post)
    monkeypatch.setattr(
        slack_reply,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "thread-1",
                "source": "slack",
                "slack_ask": True,
                "slack_thread": {"channel_id": "C1", "triggering_user_id": "U1"},
            }
        },
    )

    result = await slack_reply.slack_reply("the answer", "final")

    assert result == {"success": True}
    assert post.await_args is not None
    assert post.await_args.args == ("C1", "U1", "the answer")
    assert post.await_args.kwargs["blocks"] == [{"type": "markdown", "text": "the answer"}]
    assert post.await_args.kwargs["agent_thread_id"] == "thread-1"


def _ask_config(response_url: str) -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": "thread-1",
            "source": "slack",
            "slack_ask": True,
            "slack_ask_response_url": response_url,
            "slack_thread": {"channel_id": "C1", "triggering_user_id": "U1"},
        }
    }


@pytest.mark.asyncio
async def test_an_unreplaceable_acknowledgement_falls_back_to_a_fresh_ephemeral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_reply, "replace_slack_command_message", AsyncMock(return_value=False))
    monkeypatch.setattr(slack_reply, "post_slack_ephemeral_reply", post)
    monkeypatch.setattr(slack_reply, "claim_slack_event", AsyncMock(return_value=True))
    monkeypatch.setattr(
        slack_reply, "get_config", lambda: _ask_config("https://hooks.slack.com/commands/T1/1/x")
    )

    assert await slack_reply.slack_reply("the answer", "final") == {"success": True}

    assert post.await_args is not None
    assert post.await_args.args == ("C1", "U1", "the answer")
