import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from slack_sdk.errors import SlackApiError

from agent.slack import allowed_bots, http
from tests.support.slack_api import slack_api_server


@pytest.mark.parametrize("directory", [False, True], ids=["identity", "bot-directory"])
async def test_cached_slack_loader_survives_request_cancellation(monkeypatch, directory):
    started, release = asyncio.Event(), asyncio.Event()
    source_tokens: list[str] = []

    class Client:
        def __init__(self, token: str) -> None:
            self.token = token
            self.closed = False

        async def auth_test(self) -> SimpleNamespace:
            if not directory:
                await self.wait_for_response()
            return SimpleNamespace(data={"team_id": "T123"})

        async def users_list(self, **kwargs: object) -> dict[str, object]:
            await self.wait_for_response()
            return {
                "members": [
                    {
                        "id": "U123",
                        "team_id": "T123",
                        "is_bot": True,
                        "profile": {"bot_id": "B123", "display_name": "Release bot"},
                    }
                ]
            }

        async def wait_for_response(self) -> None:
            source_tokens.append(self.token)
            started.set()
            await release.wait()
            assert not self.closed, "the request closed the loader's session"

    clients: list[Client] = []

    @asynccontextmanager
    async def bot(*, token: str | None = None) -> AsyncIterator[Client]:
        client = Client(token or "original")
        clients.append(client)
        try:
            yield client
        finally:
            client.closed = True

    monkeypatch.setattr(http.SlackClient, "bot", bot)

    async def request() -> object:
        if directory:
            return await allowed_bots.list_slack_bots()
        async with http.SlackClient.bot() as client:
            return await http.slack_identity(client)

    first = asyncio.create_task(request())
    async with asyncio.timeout(1):
        await started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert clients[0].closed
    second = asyncio.create_task(request())
    release.set()
    async with asyncio.timeout(1):
        result = await second
    assert result == (
        [
            allowed_bots.SlackBotOption(
                team_id="T123", bot_id="B123", user_id="U123", name="Release bot"
            )
        ]
        if directory
        else {"team_id": "T123"}
    )
    assert source_tokens == ["original"]
    assert all(client.closed for client in clients)


async def test_sdk_session_sends_messages_and_closes_on_rate_limit(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-token")
    with slack_api_server() as api:
        monkeypatch.setattr(http, "SLACK_API_BASE_URL", api.base_url, raising=False)
        api.respond(
            {"ok": False, "error": "ratelimited"}, status=429, headers={"Retry-After": "30"}
        )
        with pytest.raises(SlackApiError) as raised:
            async with http.SlackClient.bot() as client:
                session = client.session
                await client.chat_postMessage(channel="C1", text="Hello", thread_ts="1.0")
        assert raised.value.response.headers["Retry-After"] == "30"
        assert api.calls == [
            ("chat.postMessage", {"channel": "C1", "text": "Hello", "thread_ts": "1.0"})
        ]
        assert session.closed


@pytest.mark.parametrize("data", [[1], 42, True, '"unexpected"'])
async def test_non_object_slack_responses_are_reported_as_upstream_errors(slack_api, data):
    slack_api.respond(data)
    with pytest.raises(HTTPException) as raised:
        async with http.slack_http_errors(), http.SlackClient.bot() as client:
            await client.users_info(user="U1")
    assert raised.value.status_code == 502


async def test_read_thread_tool_paginates_and_resolves_authors(slack_api):
    from agent.slack.tools.read_thread_messages import slack_read_thread_messages

    slack_api.respond(
        {
            "ok": True,
            "messages": [{"ts": "1.0", "user": "U1", "text": "First message"}],
            "response_metadata": {"next_cursor": "page2"},
        }
    )
    slack_api.respond(
        {"ok": True, "messages": [{"ts": "2.0", "user": "U1", "text": "Second message"}]}
    )
    slack_api.respond({"ok": True, "user": {"profile": {"display_name": "Alice"}}})
    result = await slack_read_thread_messages("C1", "1.0")
    assert result["success"] is True
    assert result["count"] == 2
    assert "Alice" in result["formatted"]
    assert result["formatted"].index("First message") < result["formatted"].index("Second message")
    assert slack_api.calls == [
        ("conversations.replies", {"channel": "C1", "ts": "1.0", "limit": "200"}),
        (
            "conversations.replies",
            {"channel": "C1", "ts": "1.0", "limit": "200", "cursor": "page2"},
        ),
        ("users.info", {"user": "U1"}),
    ]


_PUBLIC_CHANNEL = {
    "ok": True,
    "channel": {
        "id": "C1",
        "is_channel": True,
        "is_private": False,
        "is_ext_shared": False,
        "is_pending_ext_shared": False,
    },
}


async def test_read_channel_tool_marks_threads_and_skips_joins(slack_api):
    from agent.slack.tools.read_channel_messages import slack_read_channel_messages

    slack_api.respond(_PUBLIC_CHANNEL)
    slack_api.respond(
        {
            "ok": True,
            "messages": [
                {
                    "ts": "2.0",
                    "user": "U1",
                    "text": "Opened a PR",
                    "thread_ts": "2.0",
                    "reply_count": 2,
                },
                {"ts": "1.5", "user": "U2", "text": "joined", "subtype": "channel_join"},
                {"ts": "1.0", "user": "U1", "text": "Deploys are failing"},
            ],
        }
    )
    slack_api.respond({"ok": True, "user": {"profile": {"display_name": "Alice"}}})
    result = await slack_read_channel_messages("C1", limit=5)

    assert result["success"] is True
    assert result["count"] == 2
    formatted = result["formatted"]
    assert formatted.index("Deploys are failing") < formatted.index("Opened a PR")
    assert "[thread: 2 replies, thread_ts=2.0]" in formatted
    assert "joined" not in formatted
    assert ("conversations.history", {"channel": "C1", "limit": "5"}) in slack_api.calls


async def test_read_channel_tool_refuses_a_private_channel(slack_api):
    from agent.slack.tools.read_channel_messages import slack_read_channel_messages

    slack_api.respond({"ok": True, "channel": {"id": "C1", "is_channel": True, "is_private": True}})
    result = await slack_read_channel_messages("C1")

    assert result["success"] is False
    assert "public" in result["error"]
    assert [call[0] for call in slack_api.calls] == ["conversations.info"]


@pytest.mark.parametrize(
    "thread_ts,method", [("0", "conversations.history"), ("1.0", "conversations.replies")]
)
async def test_exact_message_lookup_preserves_scope(slack_api, thread_ts, method):
    from agent.slack.client import fetch_slack_thread_message_by_ts

    message = {"ts": "2.0", "text": "Exact message"}
    slack_api.respond({"ok": True, "messages": [{"ts": "1.0", "text": "Parent"}, message]})
    assert await fetch_slack_thread_message_by_ts("C1", thread_ts, "2.0") == message
    assert slack_api.calls[0][0] == method
    assert slack_api.calls[0][1]["inclusive"] == "1"
    assert slack_api.calls[0][1].get("ts") == ("1.0" if thread_ts == "1.0" else None)


async def test_existing_reaction_is_success(slack_api):
    from agent.slack.client import add_slack_reaction

    slack_api.respond({"ok": False, "error": "already_reacted"})
    assert await add_slack_reaction("C1", "1.0", "eyes")


async def test_code_channel_methods_use_shared_sdk(slack_api):
    from agent.slack.code_channels import create_code_channel

    slack_api.respond({"ok": True, "channel": {"id": "CNEW"}})
    assert await create_code_channel(
        name="Task",
        session_id="run-1",
        origin_channel_id="C1",
        origin_message_ts="1.0",
        is_private=True,
    ) == ("CNEW", None)
    assert slack_api.calls == [
        (
            "agents.conversations.create",
            {
                "name": "Task",
                "session_id": "run-1",
                "origin_channel_id": "C1",
                "origin_message_ts": "1.0",
                "is_private": True,
            },
        )
    ]


@pytest.mark.parametrize(
    "status,data,headers,expected",
    [
        (429, [], {"Retry-After": "12"}, 429),
        (429, None, {"Retry-After": "12"}, 429),
        (200, "upstream error", {"Content-Type": "text/plain"}, 502),
        (429, "upstream error", {"Content-Type": "text/plain", "Retry-After": "12"}, 429),
    ],
)
async def test_malformed_response_preserves_rate_limit_metadata(
    slack_api, status, data, headers, expected
):
    slack_api.respond(data, status=status, headers=headers)
    with pytest.raises(HTTPException) as raised:
        async with http.slack_http_errors(), http.SlackClient.bot() as client:
            await client.users_info(user="U1")
    assert raised.value.status_code == expected
    if expected == 429:
        assert raised.value.headers == {"Retry-After": "12"}
