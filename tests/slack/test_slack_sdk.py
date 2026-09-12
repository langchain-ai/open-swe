import pytest
from fastapi import HTTPException
from slack_sdk.errors import SlackApiError

from agent.slack import http
from tests.support.slack_api import slack_api_server


async def test_sdk_session_sends_messages_and_closes_on_rate_limit(monkeypatch):
    with slack_api_server() as api:
        monkeypatch.setattr(http, "SLACK_API_BASE_URL", api.base_url, raising=False)
        api.respond(
            {"ok": False, "error": "ratelimited"}, status=429, headers={"Retry-After": "30"}
        )
        with pytest.raises(SlackApiError) as raised:
            async with http.slack_client(token="test-token") as client:
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
        async with http.slack_http_errors(), http.slack_client(token="test-token") as client:
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
        async with http.slack_http_errors(), http.slack_client(token="test-token") as client:
            await client.users_info(user="U1")
    assert raised.value.status_code == expected
    if expected == 429:
        assert raised.value.headers == {"Retry-After": "12"}
