import asyncio
import threading

import pytest
from fastapi import HTTPException
from slack_sdk.errors import SlackApiError

from openswe.slack import http
from openswe.slack.client import remove_slack_reaction
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
    from openswe.slack.tools.read_thread_messages import slack_read_thread_messages

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
    from openswe.slack.tools.read_channel_messages import slack_read_channel_messages

    grant_tool_access(private=True)

    slack_api.respond({"ok": True, "channel": {"id": "C1", "is_channel": True, "is_private": True}})
    result = await slack_read_channel_messages("C1")

    assert result["success"] is False
    error = result["error"]
    assert isinstance(error, str) and "public" in error
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


async def test_channel_checks_coalesce_but_later_checks_are_fresh(slack_api: SlackAPI) -> None:
    slack_api.respond(_PUBLIC_CHANNEL)

    async def check() -> object:
        async with http.SlackClient.bot() as client:
            return (await client.conversations_info(channel="C1"))["channel"]

    results = await asyncio.gather(*(check() for _ in range(20)))
    assert all(result == _PUBLIC_CHANNEL["channel"] for result in results)
    assert len(slack_api.calls) == 1

    external = {**_PUBLIC_CHANNEL["channel"], "is_ext_shared": True}
    slack_api.respond({"ok": True, "channel": external})
    assert await check() == external
    assert len(slack_api.calls) == 2


async def test_cancelling_first_check_does_not_close_shared_request(slack_api: SlackAPI) -> None:
    started, release = threading.Event(), threading.Event()

    def respond(*_args: object) -> tuple[int, object, dict[str, str]]:
        started.set()
        assert release.wait(timeout=2)
        return 200, _PUBLIC_CHANNEL, {}

    slack_api.handler = respond

    async def check() -> object:
        async with http.SlackClient.bot() as client:
            return await client.conversations_info(channel="C1")

    first = asyncio.create_task(check())
    try:
        assert await asyncio.to_thread(started.wait, 2)
        second = asyncio.create_task(check())
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert (await second)["channel"] == _PUBLIC_CHANNEL["channel"]
        assert len(slack_api.calls) == 1
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)


async def test_channel_rate_limit_is_shared_across_replicas_but_not_tokens_or_methods(
    slack_api: SlackAPI,
    registry_db: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from sqlalchemy import text

    from openswe.database.postgres import transaction

    slack_api.respond(
        {"ok": False, "error": "ratelimited", "secret": "must-not-log"},
        status=429,
        headers={"Retry-After": "30"},
    )
    async with http.SlackClient.bot() as client:
        with pytest.raises(SlackApiError):
            await client.conversations_info(channel="C1")
        http._CHANNEL_INFO.clear()
        with pytest.raises(SlackApiError) as raised:
            await client.conversations_info(channel="COTHER")
        assert http.slack_error(raised.value).startswith("rate_limited")
        assert len(slack_api.calls) == 1
        await client.auth_test()
        monkeypatch.setenv("SLACK_BOT_TOKEN", "another-token")
        slack_api.respond(_PUBLIC_CHANNEL)
        async with http.SlackClient.bot() as other:
            await other.conversations_info(channel="C1")
        async with transaction() as conn:
            await conn.execute(text("UPDATE event_claim SET expires_at = clock_timestamp()"))
        slack_api.respond(_PUBLIC_CHANNEL)
        assert (await client.conversations_info(channel="C1"))["channel"] == _PUBLIC_CHANNEL[
            "channel"
        ]
    assert [call[0] for call in slack_api.calls] == [
        "conversations.info",
        "auth.test",
        "conversations.info",
        "conversations.info",
    ]
    failure = next(record for record in caplog.records if hasattr(record, "slack_method"))
    assert failure.slack_error == "ratelimited"
    assert failure.slack_status == 429
    assert failure.slack_retry_after == 30
    assert "must-not-log" not in repr(failure.__dict__)
    assert "test-slack-token" not in repr(failure.__dict__)


async def test_failed_channel_lookup_backs_off_without_serving_previous_permissions(
    slack_api: SlackAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 0.0
    monkeypatch.setattr(http, "monotonic", lambda: now)
    async with http.SlackClient.bot() as client:
        slack_api.respond(_PUBLIC_CHANNEL)
        await client.conversations_info(channel="C1")
        slack_api.respond({"ok": False, "error": "fatal_error"}, status=503)
        for _ in range(3):
            with pytest.raises(SlackApiError):
                await client.conversations_info(channel="C1")
        assert len(slack_api.calls) == 2
        now = 3
        external = {**_PUBLIC_CHANNEL["channel"], "is_ext_shared": True}
        slack_api.respond({"ok": True, "channel": external})
        assert (await client.conversations_info(channel="C1"))["channel"] == external
        assert len(slack_api.calls) == 3


@pytest.mark.parametrize(
    "status,code,delay",
    [
        (429, "ratelimited", 17),
        (503, "unavailable", 60),
        (200, "internal_error", 60),
        (200, "channel_not_found", None),
        (200, "not_in_channel", None),
        (200, "missing_scope", None),
        (403, "invalid_auth", None),
    ],
)
async def test_channel_context_distinguishes_transient_and_definitive_failures(
    slack_api: SlackAPI,
    status: int,
    code: str,
    delay: int | None,
) -> None:
    from openswe.webhooks.common import resolve_slack_channel_context

    slack_api.respond(
        {"ok": False, "error": code},
        status=status,
        headers={"Retry-After": "17"} if status == 429 else {},
    )
    context = await resolve_slack_channel_context("C1", use_cache=False)
    assert not context.allows_operations
    assert context.lookup_retry_after == delay
    assert "lookup_retry_after" not in context.dump()
