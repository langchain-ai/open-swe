import pytest
from fastapi import HTTPException
from slack_sdk.errors import SlackApiError

from agent.slack import http
from agent.slack.client import remove_slack_reaction
from agent.tools.errors import ToolError
from tests.support.slack_api import SlackAPI, slack_api_server


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


async def test_removing_an_absent_reaction_is_already_settled(slack_api: SlackAPI) -> None:
    slack_api.respond({"ok": False, "error": "no_reaction"})
    assert await remove_slack_reaction("C1", "1.0", "x")
    slack_api.respond({"ok": False, "error": "missing_scope"})
    assert not await remove_slack_reaction("C1", "1.0", "x")


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


async def test_read_channel_tool_refuses_a_private_channel(slack_api, grant_tool_access):  # noqa: ANN001
    from agent.slack.tools.read_channel_messages import slack_read_channel_messages

    grant_tool_access(private=True)

    slack_api.respond({"ok": True, "channel": {"id": "C1", "is_channel": True, "is_private": True}})
    with pytest.raises(ToolError) as raised:
        await slack_read_channel_messages("C1")

    assert "public" in str(raised.value)
    assert [call[0] for call in slack_api.calls] == ["conversations.info"]


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
