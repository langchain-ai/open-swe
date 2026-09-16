from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from fastapi import BackgroundTasks, Request

from agent.slack import ask as slack_ask
from agent.slack import routes as slack_routes
from agent.slack.tools import thread_reply as slack_thread_reply
from agent.threads.listing import _metadata_matches_filters
from tests.support.repositories import FakeRepositories


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
async def test_command_queues_the_question(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        slack_routes,
        "dashboard_thread_url",
        lambda thread_id: f"https://swe.test/agents/{thread_id}",
    )
    background_tasks = BackgroundTasks()

    result = await slack_routes.slack_command(
        _command_request("how does thread routing work?"), background_tasks
    )

    assert result["response_type"] == "ephemeral"
    assert len(background_tasks.tasks) == 1
    queued = background_tasks.tasks[0]
    assert queued.func is slack_ask.process_slack_ask
    request = queued.args[0]
    assert request.question == "how does thread routing work?"
    assert request.channel_id == "C1"
    assert request.user_id == "U1"
    # The acknowledgement links to the thread the queued run will use.
    assert request.thread_id == slack_ask.ask_thread_id("C1", "U1")
    assert request.thread_id in result["text"]


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
    monkeypatch.setattr(slack_ask.common, "login_for_slack_id", AsyncMock(return_value="octocat"))
    monkeypatch.setattr(slack_ask.common, "get_valid_access_token", AsyncMock(return_value="gho_x"))
    monkeypatch.setattr(slack_ask, "get_thread_active_status", AsyncMock(return_value=False))


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


@pytest.mark.asyncio
@pytest.mark.usefixtures("linked_asker")
async def test_a_command_joins_work_already_running(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = AsyncMock(return_value=True)
    dispatch = AsyncMock()
    monkeypatch.setattr(
        slack_ask.common,
        "get_slack_repo_config",
        AsyncMock(return_value=slack_ask.common.SlackRepoResolution()),
    )
    monkeypatch.setattr(
        slack_ask.common, "upsert_agent_thread_metadata", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(slack_ask, "get_thread_active_status", AsyncMock(return_value=True))
    monkeypatch.setattr(slack_ask, "queue_message_for_thread", queue)
    monkeypatch.setattr(slack_ask, "post_slack_ephemeral_message", AsyncMock(return_value=True))
    monkeypatch.setattr(slack_ask, "dispatch_agent_run", dispatch)

    await slack_ask.process_slack_ask(
        slack_ask.SlackAskRequest(
            channel_id="C1", user_id="U1", question="and the other one?", thread_id="t-1"
        )
    )

    dispatch.assert_not_awaited()
    assert "and the other one?" in queue.await_args.args[1][0]["text"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("linked_asker")
async def test_named_repository_picks_the_workspace(
    monkeypatch: pytest.MonkeyPatch, fake_repositories: FakeRepositories
) -> None:
    resolve_workspace = AsyncMock(return_value=SimpleNamespace(slug="payments"))
    api = fake_repositories.add("acme/api")
    monkeypatch.setattr(
        slack_ask.common,
        "get_slack_repo_config",
        AsyncMock(return_value=slack_ask.common.SlackRepoResolution(repos=(api,), explicit=True)),
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

    assert resolve_workspace.await_args.kwargs["repositories"] == (api,)
    assert dispatch.await_args.args[2]["workspace"] == "payments"


def test_unlisted_threads_stay_out_of_the_thread_list() -> None:
    filters: dict[str, Any] = {"resolved": None, "source": None, "query": None}

    assert _metadata_matches_filters({"source": "slack"}, **filters)
    assert not _metadata_matches_filters({"source": "slack", "unlisted": True}, **filters)


@pytest.mark.asyncio
async def test_ask_mode_reply_is_ephemeral(monkeypatch: pytest.MonkeyPatch) -> None:
    post = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_thread_reply, "post_slack_ephemeral_reply", post)
    monkeypatch.setattr(
        slack_thread_reply,
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

    result = await slack_thread_reply.slack_thread_reply("the answer")

    assert result == {"success": True}
    assert post.await_args.args == ("C1", "U1", "the answer")
    assert post.await_args.kwargs["agent_thread_id"] == "thread-1"
