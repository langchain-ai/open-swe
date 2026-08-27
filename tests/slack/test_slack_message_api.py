"""Tests for Slack message API utilities."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agent.utils import slack as slack_utils


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
async def test_thinking_steps_stream_api_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")
    client_cm = _async_client_cm(_ok_response())
    chunks = [{"type": "task_update", "id": "step-1", "title": "Reading", "status": "in_progress"}]

    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        started = await slack_utils.start_slack_stream(
            "C1", "1.0", chunks, recipient_user_id="U1", recipient_team_id="T1"
        )
        appended = await slack_utils.append_slack_stream("C1", "1.0", chunks)
        stopped = await slack_utils.stop_slack_stream("C1", "1.0")

    assert started == ("1.0", None)
    assert appended == (True, None)
    assert stopped == (True, None)
    calls = client_cm.post.await_args_list
    assert calls[0].args[0].endswith("/chat.startStream")
    assert calls[0].kwargs["json"] == {
        "channel": "C1",
        "chunks": chunks,
        "task_display_mode": "timeline",
        "thread_ts": "1.0",
        "recipient_user_id": "U1",
        "recipient_team_id": "T1",
    }
    assert calls[1].args[0].endswith("/chat.appendStream")
    assert calls[2].kwargs["json"]["session_status"] == "active"


@pytest.mark.asyncio
async def test_code_channel_stream_is_top_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")
    client_cm = _async_client_cm(_ok_response())

    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        await slack_utils.start_slack_stream("C1", "0", [])

    assert "thread_ts" not in client_cm.post.await_args.kwargs["json"]


@pytest.mark.asyncio
async def test_update_slack_message_calls_chat_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_ok_response())
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_utils.update_slack_message(
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
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "")

    client_cm = _async_client_cm(_ok_response())
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_utils.post_slack_thread_reply_with_ts("C1", "1.0", "hello")

    assert result == (None, "missing_slack_bot_token")
    client_cm.post.assert_not_called()


@pytest.mark.asyncio
async def test_post_slack_thread_reply_with_ts_returns_slack_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_err_response("msg_too_long"))
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_utils.post_slack_thread_reply_with_ts("C1", "1.0", "hello")

    assert result == (None, "msg_too_long")


@pytest.mark.asyncio
async def test_post_slack_thread_reply_with_ts_returns_rate_limited_with_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_rate_limited_response(retry_after="30"))
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_utils.post_slack_thread_reply_with_ts("C1", "1.0", "hello")

    assert result == (None, "rate_limited: 30")


@pytest.mark.asyncio
async def test_post_slack_thread_reply_with_ts_returns_rate_limited_without_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_rate_limited_response())
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_utils.post_slack_thread_reply_with_ts("C1", "1.0", "hello")

    assert result == (None, "rate_limited")


@pytest.mark.asyncio
async def test_post_slack_thread_reply_with_ts_normalizes_ratelimited_body_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_err_response("ratelimited"))
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_utils.post_slack_thread_reply_with_ts("C1", "1.0", "hello")

    assert result == (None, "rate_limited")


@pytest.mark.asyncio
async def test_post_slack_thread_reply_with_ts_returns_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_ok_response())
    client_cm.post = AsyncMock(side_effect=slack_utils.httpx.ConnectError("boom"))
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_utils.post_slack_thread_reply_with_ts("C1", "1.0", "hello")

    assert result == (None, "http_error: ConnectError")


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_post_slack_thread_reply_with_ts_sends_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "Pick"}}]
    client_cm = _async_client_cm(_ok_response())
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_utils.post_slack_thread_reply_with_ts(
            "C1", "1.0", "Pick", blocks=blocks, agent_thread_id="mapped-thread"
        )

    assert result == ("1.0", None)
    payload = client_cm.post.call_args.kwargs["json"]
    expected_footer = f"<{slack_utils.dashboard_thread_url('mapped-thread')}|Open in Web>"
    assert payload["text"] == f"Pick {expected_footer}"
    assert payload["blocks"] == [
        *blocks,
        {"type": "context", "elements": [{"type": "mrkdwn", "text": expected_footer}]},
    ]


@pytest.mark.asyncio
async def test_post_slack_top_level_message_with_ts_omits_thread_ts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_ok_response())
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_utils.post_slack_top_level_message_with_ts("C1", "hello")

    assert result == ("1.0", None)
    payload = client_cm.post.call_args.kwargs["json"]
    assert payload["channel"] == "C1"
    assert payload["text"] == "hello"
    assert "thread_ts" not in payload


@pytest.mark.asyncio
async def test_post_slack_top_level_message_with_ts_returns_slack_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_err_response("msg_too_long"))
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_utils.post_slack_top_level_message_with_ts("C1", "hello")

    assert result == (None, "msg_too_long")


async def test_post_slack_thread_reply_preserves_bool_return_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_err_response("channel_not_found"))
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        ok = await slack_utils.post_slack_thread_reply("C1", "1.0", "hello")

    assert ok is False


async def test_post_slack_thread_reply_forwards_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    post_with_ts = AsyncMock(return_value=("1.1", None))
    monkeypatch.setattr(slack_utils, "post_slack_thread_reply_with_ts", post_with_ts)
    blocks = [{"type": "context", "elements": [{"type": "mrkdwn", "text": "_Status_"}]}]

    ok = await slack_utils.post_slack_thread_reply("C1", "1.0", "Status", blocks=blocks)

    assert ok is True
    post_with_ts.assert_awaited_once_with("C1", "1.0", "Status", blocks=blocks)


@pytest.mark.asyncio
async def test_upload_slack_thread_file_rejects_content_over_16_mb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    result = await slack_utils.upload_slack_thread_file(
        "C1",
        "1.0",
        "plan.html",
        b"x" * (slack_utils.SLACK_FILE_UPLOAD_MAX_BYTES + 1),
    )

    assert result == (None, "file_too_large")


@pytest.mark.asyncio
async def test_upload_slack_thread_file_completes_external_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")
    ticket = MagicMock(status_code=200, headers={})
    ticket.raise_for_status.return_value = None
    ticket.json.return_value = {
        "ok": True,
        "upload_url": "https://files.slack.com/upload/v1/test",
        "file_id": "F1",
    }
    complete = MagicMock(status_code=200, headers={})
    complete.raise_for_status.return_value = None
    complete.json.return_value = {"ok": True, "files": [{"id": "F1"}]}
    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = client_cm
    client_cm.post = AsyncMock(side_effect=[ticket, complete])
    uploaded = httpx.Response(
        200,
        request=httpx.Request("POST", "https://files.slack.com"),
        text="OK - 8",
    )
    safe_request = AsyncMock(return_value=(uploaded, None))
    monkeypatch.setattr(slack_utils, "request_with_safe_redirects", safe_request)

    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_utils.upload_slack_thread_file(
            "C1", "1.0", "plan.html", b"<html />", title="Plan", initial_comment="Preview"
        )

    assert result == ("F1", None)
    ticket_call = client_cm.post.call_args_list[0]
    assert ticket_call.args[0].endswith("/files.getUploadURLExternal")
    assert ticket_call.kwargs["data"] == {
        "filename": "plan.html",
        "length": "8",
    }
    assert ticket_call.kwargs["headers"] == {
        "Authorization": "Bearer xoxb-test",
    }
    safe_request.assert_awaited_once()
    assert safe_request.call_args.kwargs["content"] == b"<html />"
    assert safe_request.call_args.kwargs["validate_url"] is slack_utils._validate_slack_upload_url
    complete_call = client_cm.post.call_args_list[1]
    assert complete_call.args[0].endswith("/files.completeUploadExternal")
    assert complete_call.kwargs["data"] == {
        "files": '[{"id": "F1", "title": "Plan"}]',
        "channel_id": "C1",
        "thread_ts": "1.0",
        "initial_comment": "Preview",
    }
    assert complete_call.kwargs["headers"] == {
        "Authorization": "Bearer xoxb-test",
    }


@pytest.mark.asyncio
async def test_upload_slack_thread_file_handles_malformed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")
    ticket = MagicMock(status_code=200, headers={})
    ticket.raise_for_status.return_value = None
    ticket.json.side_effect = ValueError("invalid JSON")
    client_cm = _async_client_cm(ticket)

    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_utils.upload_slack_thread_file("C1", "1.0", "plan.html", b"x")

    assert result == (None, "invalid_slack_response")


@pytest.mark.asyncio
async def test_upload_slack_thread_file_rejects_unsafe_upload_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")
    ticket = MagicMock(status_code=200, headers={})
    ticket.raise_for_status.return_value = None
    ticket.json.return_value = {
        "ok": True,
        "upload_url": "https://attacker.example/upload",
        "file_id": "F1",
    }
    client_cm = _async_client_cm(ticket)
    blocked = {"content": "blocked"}
    safe_request = AsyncMock(return_value=(None, blocked))
    monkeypatch.setattr(slack_utils, "request_with_safe_redirects", safe_request)

    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        result = await slack_utils.upload_slack_thread_file("C1", "1.0", "plan.html", b"x")

    assert result == (None, "unsafe_upload_url")
    assert client_cm.post.await_count == 1


def test_validate_slack_upload_url() -> None:
    assert slack_utils._validate_slack_upload_url("https://files.slack.com/upload/v1/test") == (
        True,
        "",
    )
    allowed, _ = slack_utils._validate_slack_upload_url("https://files.slack.com.evil.test/x")
    assert allowed is False
    allowed, _ = slack_utils._validate_slack_upload_url("https://edge.slack.com/x")
    assert allowed is False
    allowed, _ = slack_utils._validate_slack_upload_url("http://files.slack.com/x")
    assert allowed is False
    allowed, _ = slack_utils._validate_slack_upload_url("https://files.slack.com:8443/x")
    assert allowed is False
