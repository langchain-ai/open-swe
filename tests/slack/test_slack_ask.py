from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from fastapi import BackgroundTasks, Request

from agent.run_config import Repo
from agent.slack import ask as slack_ask
from agent.slack import client as slack_client
from agent.slack import routes as slack_routes
from agent.slack.tools import reply as slack_reply
from agent.threads.listing import _metadata_matches_filters


def _command_request(text: str, command: str = "/oswe") -> Request:
    body = urlencode(
        {
            "channel_id": "C1",
            "user_id": "U1",
            "team_id": "T1",
            "trigger_id": "trigger-1",
            "command": command,
            "text": text,
        }
    ).encode()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {"type": "http", "method": "POST", "path": "/webhooks/slack/commands", "headers": []},
        receive,
    )


@pytest.fixture
def signed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_routes.common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(slack_routes.common, "claim_slack_event", AsyncMock(return_value=True))


@pytest.mark.asyncio
@pytest.mark.usefixtures("signed")
async def test_command_queues_the_question() -> None:
    background_tasks = BackgroundTasks()

    result = await slack_routes.slack_command(
        _command_request("how does thread routing work?"), background_tasks
    )

    assert result.status_code == 200
    assert result.body == b""
    assert len(background_tasks.tasks) == 1
    queued = background_tasks.tasks[0]
    assert queued.func is slack_ask.process_slack_ask
    request = queued.args[0]
    assert request.question == "how does thread routing work?"
    assert request.channel_id == "C1"
    assert request.user_id == "U1"
    assert request.thread_id == slack_ask.ask_thread_id("C1", "U1", "trigger-1")


@pytest.mark.asyncio
@pytest.mark.usefixtures("signed")
async def test_command_without_a_question_explains_itself() -> None:
    background_tasks = BackgroundTasks()

    result = await slack_routes.slack_command(_command_request(""), background_tasks)

    assert "/oswe" in result["text"]
    assert background_tasks.tasks == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("signed")
async def test_command_refuses_an_oversized_question() -> None:
    background_tasks = BackgroundTasks()

    result = await slack_routes.slack_command(
        _command_request("x" * (slack_ask.MAX_QUESTION_CHARS + 1)), background_tasks
    )

    assert "too long" in result["text"]
    assert background_tasks.tasks == []


@pytest.fixture
def linked_asker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        slack_ask.common, "resolve_slack_channel_context", AsyncMock(return_value={"name": "eng"})
    )
    monkeypatch.setattr(slack_ask, "slack_channel_allows_operations", lambda _context: True)
    monkeypatch.setattr(slack_ask, "get_slack_user_info", AsyncMock(return_value=None))
    monkeypatch.setattr(slack_ask.User, "login_for_slack", AsyncMock(return_value="octocat"))
    monkeypatch.setattr(slack_ask.common, "get_valid_access_token", AsyncMock(return_value="gho_x"))
    monkeypatch.setattr(slack_ask, "fetch_slack_channel_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(slack_ask, "get_slack_user_names", AsyncMock(return_value={}))


@pytest.mark.asyncio
@pytest.mark.usefixtures("linked_asker")
async def test_command_thread_is_private_and_unlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    upsert = AsyncMock(return_value=True)
    dispatch = AsyncMock()
    monkeypatch.setattr(
        slack_ask.common,
        "get_slack_repo_config",
        AsyncMock(return_value=slack_ask.common.SlackRepoResolution()),
    )
    monkeypatch.setattr(slack_ask.common, "upsert_agent_thread_metadata", upsert)
    monkeypatch.setattr(slack_ask, "dispatch_agent_run", dispatch)

    await slack_ask.process_slack_ask(
        slack_ask.SlackAskRequest(
            channel_id="C1", user_id="U1", question="why?", thread_id="t-1", team_id="T1"
        )
    )

    assert upsert.await_args.kwargs["unlisted"] is True
    assert upsert.await_args.kwargs["visibility"] == "private"
    assert upsert.await_args.kwargs["owner_login"] == "octocat"
    configurable = dispatch.await_args.args[2]
    assert configurable["slack_ask"] is True
    assert configurable["slack_thread"]["triggering_user_id"] == "U1"
    assert "thread_ts" not in configurable["slack_thread"]


def test_each_invocation_gets_its_own_thread() -> None:
    first = slack_ask.ask_thread_id("C1", "U1", "trigger-1")

    assert slack_ask.ask_thread_id("C1", "U1", "trigger-2") != first
    assert slack_ask.ask_thread_id("C1", "U1", "trigger-1") == first


@pytest.mark.asyncio
@pytest.mark.usefixtures("linked_asker")
async def test_named_repository_picks_the_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    resolve_workspace = AsyncMock(return_value=SimpleNamespace(slug="payments"))
    monkeypatch.setattr(
        slack_ask.common,
        "get_slack_repo_config",
        AsyncMock(
            return_value=slack_ask.common.SlackRepoResolution(
                repo=Repo(owner="acme", name="api"), explicit=True
            )
        ),
    )
    monkeypatch.setattr(slack_ask, "resolve_workspace", resolve_workspace)
    monkeypatch.setattr(
        slack_ask.common, "upsert_agent_thread_metadata", AsyncMock(return_value=True)
    )
    dispatch = AsyncMock()
    monkeypatch.setattr(slack_ask, "dispatch_agent_run", dispatch)

    await slack_ask.process_slack_ask(
        slack_ask.SlackAskRequest(channel_id="C1", user_id="U1", question="why?", thread_id="t-2")
    )

    assert resolve_workspace.await_args.kwargs["repo"] == ("acme", "api")
    assert dispatch.await_args.args[2]["workspace"] == "payments"


@pytest.mark.asyncio
@pytest.mark.usefixtures("linked_asker")
async def test_channel_context_reaches_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        slack_ask.common,
        "get_slack_repo_config",
        AsyncMock(return_value=slack_ask.common.SlackRepoResolution()),
    )
    monkeypatch.setattr(
        slack_ask.common, "upsert_agent_thread_metadata", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        slack_ask,
        "fetch_slack_channel_messages",
        AsyncMock(
            return_value=[
                {"ts": "1.000001", "user": "U2", "text": "deploys are failing"},
                {
                    "ts": "2.000002",
                    "user": "U3",
                    "text": "opened a PR",
                    "thread_ts": "2.000002",
                    "reply_count": 3,
                },
            ]
        ),
    )
    monkeypatch.setattr(slack_ask, "get_slack_user_names", AsyncMock(return_value={"U2": "ada"}))
    dispatch = AsyncMock()
    monkeypatch.setattr(slack_ask, "dispatch_agent_run", dispatch)

    await slack_ask.process_slack_ask(
        slack_ask.SlackAskRequest(
            channel_id="C1", user_id="U1", question="what broke?", thread_id="t-3"
        )
    )

    prompt = dispatch.await_args.args[1]
    assert prompt.startswith("<markdown>")
    assert prompt.endswith("</markdown>")
    assert "@ada(U2)" in prompt
    assert "deploys are failing" in prompt
    assert "[thread: 3 replies, thread_ts=2.000002]" in prompt


@pytest.mark.asyncio
@pytest.mark.usefixtures("linked_asker")
async def test_channel_context_stays_inside_its_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        slack_ask,
        "fetch_slack_channel_messages",
        AsyncMock(
            return_value=[
                {"ts": f"{index}.000000", "user": "U2", "text": "x" * 4000}
                for index in range(1, 30)
            ]
        ),
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

    result = await slack_reply.slack_reply("the answer")

    assert result == {"success": True}
    assert post.await_args.args == ("C1", "U1", "the answer")
    assert post.await_args.kwargs["agent_thread_id"] == "thread-1"


@pytest.mark.asyncio
async def test_ask_mode_refuses_options(monkeypatch: pytest.MonkeyPatch) -> None:
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

    refused = await slack_reply.slack_reply("pick one", options=["a", "b"])

    assert refused["success"] is False
    assert refused["retry"] is True
    post.assert_not_awaited()


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
async def test_the_first_ask_reply_replaces_the_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replace = AsyncMock(return_value=True)
    post = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_reply, "replace_slack_command_message", replace)
    monkeypatch.setattr(slack_reply, "post_slack_ephemeral_reply", post)
    monkeypatch.setattr(slack_reply, "claim_slack_event", AsyncMock(return_value=True))
    monkeypatch.setattr(
        slack_reply, "get_config", lambda: _ask_config("https://hooks.slack.com/commands/T1/1/x")
    )

    assert await slack_reply.slack_reply("the answer") == {"success": True}

    assert replace.await_args.args[0] == "https://hooks.slack.com/commands/T1/1/x"
    assert replace.await_args.args[1] == "the answer"
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_later_ask_reply_posts_instead_of_replacing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replace = AsyncMock(return_value=True)
    post = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_reply, "replace_slack_command_message", replace)
    monkeypatch.setattr(slack_reply, "post_slack_ephemeral_reply", post)
    # The acknowledgement is already spoken for, so the claim fails.
    monkeypatch.setattr(slack_reply, "claim_slack_event", AsyncMock(return_value=False))
    monkeypatch.setattr(
        slack_reply, "get_config", lambda: _ask_config("https://hooks.slack.com/commands/T1/1/x")
    )

    assert await slack_reply.slack_reply("a follow-up") == {"success": True}

    replace.assert_not_awaited()
    assert post.await_args.args == ("C1", "U1", "a follow-up")


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

    assert await slack_reply.slack_reply("the answer") == {"success": True}

    assert post.await_args.args == ("C1", "U1", "the answer")


@pytest.mark.asyncio
async def test_the_acknowledgement_only_goes_out_through_the_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any], str]] = []

    async def capture(response_url: str, payload: dict[str, Any], path_prefix: str) -> bool:
        calls.append((response_url, payload, path_prefix))
        return True

    monkeypatch.setattr(slack_ask, "post_slack_ephemeral_message", AsyncMock(return_value=True))
    monkeypatch.setattr(slack_client, "_post_slack_callback", capture)

    assert await slack_client.acknowledge_slack_command(
        "https://hooks.slack.com/commands/T1/1/x", "Working on it"
    )

    response_url, payload, prefix = calls[0]
    assert prefix == "/commands/"
    assert payload == {"response_type": "ephemeral", "text": "Working on it"}
    # No replace_original: this message is the one later replies replace.
    assert "replace_original" not in payload
