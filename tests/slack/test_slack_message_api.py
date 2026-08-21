"""Tests for Slack message API utilities."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.utils import slack_api


def _ok_response() -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"ok": True, "ts": "1.0"}
    response.raise_for_status.return_value = None
    return response


def _err_response(error: str = "channel_not_found") -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"ok": False, "error": error}
    response.raise_for_status.return_value = None
    return response


def _rate_limited_response(retry_after: str | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = 429
    response.headers = {"Retry-After": retry_after} if retry_after else {}
    return response


def _async_client_cm(post_response: MagicMock) -> AsyncMock:
    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = client_cm
    client_cm.post = AsyncMock(return_value=post_response)
    return client_cm


@pytest.mark.asyncio
async def test_update_slack_message_calls_chat_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_ok_response())
    with patch.object(slack_api.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_api.update_slack_message(
            "C1", "1.1", "moved", unfurl_links=False, unfurl_media=False
        )

    assert result == (True, None)
    assert client_cm.post.await_count == 1
    assert client_cm.post.call_args.args[0].endswith("/chat.update")
    assert client_cm.post.call_args.kwargs["json"] == {
        "channel": "C1",
        "ts": "1.1",
        "text": "moved",
        "unfurl_links": False,
        "unfurl_media": False,
    }


@pytest.mark.asyncio
async def test_post_slack_thread_reply_with_ts_returns_missing_token_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)

    client_cm = _async_client_cm(_ok_response())
    with patch.object(slack_api.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_api.post_slack_thread_reply_with_ts("C1", "1.0", "hello")

    assert result == (None, "missing_slack_bot_token")
    client_cm.post.assert_not_called()


@pytest.mark.asyncio
async def test_post_slack_thread_reply_with_ts_returns_slack_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_err_response("msg_too_long"))
    with patch.object(slack_api.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_api.post_slack_thread_reply_with_ts("C1", "1.0", "hello")

    assert result == (None, "msg_too_long")


@pytest.mark.asyncio
async def test_post_slack_thread_reply_with_ts_returns_rate_limited_with_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_rate_limited_response(retry_after="30"))
    with patch.object(slack_api.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_api.post_slack_thread_reply_with_ts("C1", "1.0", "hello")

    assert result == (None, "rate_limited: 30")


@pytest.mark.asyncio
async def test_post_slack_thread_reply_with_ts_returns_rate_limited_without_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_rate_limited_response())
    with patch.object(slack_api.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_api.post_slack_thread_reply_with_ts("C1", "1.0", "hello")

    assert result == (None, "rate_limited")


@pytest.mark.asyncio
async def test_post_slack_thread_reply_with_ts_normalizes_ratelimited_body_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_err_response("ratelimited"))
    with patch.object(slack_api.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_api.post_slack_thread_reply_with_ts("C1", "1.0", "hello")

    assert result == (None, "rate_limited")


@pytest.mark.asyncio
async def test_post_slack_thread_reply_with_ts_returns_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_ok_response())
    client_cm.post = AsyncMock(side_effect=slack_api.httpx.ConnectError("boom"))
    with patch.object(slack_api.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_api.post_slack_thread_reply_with_ts("C1", "1.0", "hello")

    assert result == (None, "http_error: ConnectError")


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_post_slack_thread_reply_with_ts_sends_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")

    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "Pick"}}]
    client_cm = _async_client_cm(_ok_response())
    with patch.object(slack_api.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_api.post_slack_thread_reply_with_ts(
            "C1", "1.0", "Pick", blocks=blocks, agent_thread_id="mapped-thread"
        )

    assert result == ("1.0", None)
    payload = client_cm.post.call_args.kwargs["json"]
    expected_footer = f"<{slack_api.dashboard_thread_url('mapped-thread')}|Open in Web>"
    assert payload["text"] == f"Pick {expected_footer}"
    assert payload["blocks"] == [
        *blocks,
        {"type": "context", "elements": [{"type": "mrkdwn", "text": expected_footer}]},
    ]


@pytest.mark.asyncio
async def test_post_slack_top_level_message_with_ts_omits_thread_ts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_ok_response())
    with patch.object(slack_api.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_api.post_slack_top_level_message_with_ts("C1", "hello")

    assert result == ("1.0", None)
    payload = client_cm.post.call_args.kwargs["json"]
    assert payload["channel"] == "C1"
    assert payload["text"] == "hello"
    assert "thread_ts" not in payload


@pytest.mark.asyncio
async def test_post_slack_top_level_message_with_ts_returns_slack_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_err_response("msg_too_long"))
    with patch.object(slack_api.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_api.post_slack_top_level_message_with_ts("C1", "hello")

    assert result == (None, "msg_too_long")


async def test_post_slack_thread_reply_preserves_bool_return_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_err_response("channel_not_found"))
    with patch.object(slack_api.httpx, "AsyncClient", return_value=client_cm):
        ok = await slack_api.post_slack_thread_reply("C1", "1.0", "hello")

    assert ok is False


async def test_post_slack_thread_reply_forwards_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    post_with_ts = AsyncMock(return_value=("1.1", None))
    monkeypatch.setattr(slack_api, "post_slack_thread_reply_with_ts", post_with_ts)
    blocks = [{"type": "context", "elements": [{"type": "mrkdwn", "text": "_Status_"}]}]

    ok = await slack_api.post_slack_thread_reply("C1", "1.0", "Status", blocks=blocks)

    assert ok is True
    post_with_ts.assert_awaited_once_with("C1", "1.0", "Status", blocks=blocks)
