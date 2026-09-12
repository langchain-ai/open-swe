"""Slack message behavior exercised through the SDK's real HTTP requests."""

import json
import logging
from unittest.mock import patch

import httpx2
import pytest

from agent.slack import client as slack_utils


def test_parse_slack_thread_url_uses_root_thread_timestamp() -> None:
    assert slack_utils.parse_slack_thread_url(
        "<https://workspace.slack.com/archives/C123/p1788431248678809"
        "?thread_ts=1788425314.774339&cid=C123|message>"
    ) == ("C123", "1788425314.774339")


def test_parse_slack_thread_url_defaults_to_message_timestamp() -> None:
    assert slack_utils.parse_slack_thread_url(
        "https://workspace.slack.com/archives/C123/p1788431248678809"
    ) == ("C123", "1788431248.678809")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body,success",
    [
        (200, "ok", True),
        (200, '{"ok":true}', True),
        (200, '{"ok":false}', False),
        (200, "invalid", False),
        (410, "expired", False),
        (302, "", False),
    ],
)
async def test_interaction_response_replaces_private_message_without_bot_auth(
    status: int, body: str, success: bool, caplog: pytest.LogCaptureFixture
) -> None:
    url = "https://hooks.slack.com/actions/T1/B1/test-response"
    message = {"replace_original": True, "text": "Saved", "blocks": []}
    requests: list[httpx2.Request] = []

    async def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(status, text=body)

    transport = httpx2.MockTransport(handle)
    caplog.set_level(logging.DEBUG, logger="httpx2")
    with patch.object(slack_utils.httpx2, "AsyncHTTPTransport", return_value=transport):
        assert await slack_utils.respond_to_slack_interaction(url, message) is success
    assert len(requests) == 1
    assert str(requests[0].url) == url
    assert json.loads(requests[0].content) == message
    assert "Authorization" not in requests[0].headers
    assert "test-response" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.slack.com/actions/test",
        "https://example.com/actions/test",
        "https://hooks.slack.com.example.com/actions/test",
        "https://hooks.slack.com/api/test",
        "https://hooks.slack.com@127.0.0.1/actions/test",
        "https://[invalid",
    ],
)
async def test_interaction_response_rejects_non_slack_urls(url: str) -> None:
    with patch.object(slack_utils.httpx2, "AsyncHTTPTransport") as client:
        assert not await slack_utils.respond_to_slack_interaction(url, {"delete_original": True})
    client.assert_not_called()


@pytest.mark.asyncio
async def test_interaction_response_failure_does_not_log_capability_url(
    caplog: pytest.LogCaptureFixture,
) -> None:
    url = "https://hooks.slack.com/actions/T1/B1/test-response"

    async def handle(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"Failed {url}")

    caplog.set_level(logging.DEBUG, logger="httpx2")
    with patch.object(
        slack_utils.httpx2, "AsyncHTTPTransport", return_value=httpx2.MockTransport(handle)
    ):
        assert not await slack_utils.respond_to_slack_interaction(url, {"delete_original": True})
    assert "test-response" not in caplog.text


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


async def test_ephemeral_feedback_sends_blocks_and_thread(slack_api):
    blocks = [{"type": "section", "text": {"type": "plain_text", "text": "Rate this thread"}}]
    assert await slack_utils.post_slack_ephemeral_message(
        "C1", "U1", "Rate this thread", "1.0", blocks=blocks
    )
    assert slack_api.calls == [
        (
            "chat.postEphemeral",
            {
                "channel": "C1",
                "user": "U1",
                "text": "Rate this thread",
                "thread_ts": "1.0",
                "blocks": blocks,
            },
        )
    ]


async def test_modal_sends_trigger_and_view(slack_api):
    view = {"type": "modal", "title": {"type": "plain_text", "text": "Feedback"}, "blocks": []}
    assert await slack_utils.open_slack_modal("trigger-1", view)
    assert slack_api.calls == [("views.open", {"trigger_id": "trigger-1", "view": view})]


@pytest.mark.parametrize(
    "status,data",
    [
        (200, {"ok": False, "error": "expired_trigger_id"}),
        (429, {"ok": False}),
        (503, "unavailable"),
    ],
)
async def test_modal_returns_false_on_failure(slack_api, status, data):
    slack_api.respond(data, status=status)
    assert not await slack_utils.open_slack_modal("trigger-1", {})
    assert len(slack_api.calls) == 1


async def test_thinking_steps_stream_api_payloads(slack_api):
    chunks = [{"type": "task_update", "id": "step-1", "title": "Reading", "status": "in_progress"}]
    assert (
        await slack_utils.start_slack_stream(
            "C1", "1.0", chunks, recipient_user_id="U1", recipient_team_id="T1"
        )
        == "1.0"
    )
    await slack_utils.append_slack_stream("C1", "1.0", chunks)
    await slack_utils.stop_slack_stream("C1", "1.0", chunks)
    assert slack_api.calls == [
        (
            "chat.startStream",
            {
                "channel": "C1",
                "chunks": chunks,
                "task_display_mode": "plan",
                "thread_ts": "1.0",
                "recipient_user_id": "U1",
                "recipient_team_id": "T1",
            },
        ),
        ("chat.appendStream", {"channel": "C1", "ts": "1.0", "chunks": chunks}),
        (
            "chat.stopStream",
            {"channel": "C1", "ts": "1.0", "chunks": chunks, "session_status": "active"},
        ),
    ]


async def test_code_channel_stream_is_top_level(slack_api):
    await slack_utils.start_slack_stream("C1", "0", [])
    assert "thread_ts" not in slack_api.calls[0][1]


async def test_slack_stream_rate_limit_preserves_retry_after(slack_api):
    slack_api.respond({"ok": False}, status=429, headers={"Retry-After": "30"})
    with pytest.raises(slack_utils.SlackStreamError) as raised:
        await slack_utils.append_slack_stream("C1", "1.0", [])
    assert raised.value.code == "rate_limited"
    assert raised.value.retry_after == 30
    assert len(slack_api.calls) == 1


async def test_update_slack_message_can_clear_blocks(slack_api):
    assert await slack_utils.update_slack_message(
        "C1", "1.1", "moved", unfurl_links=False, unfurl_media=False, blocks=[]
    ) == (True, None)
    assert slack_api.calls == [
        (
            "chat.update",
            {
                "channel": "C1",
                "ts": "1.1",
                "text": "moved",
                "unfurl_links": False,
                "unfurl_media": False,
                "blocks": [],
            },
        )
    ]


@pytest.mark.parametrize(
    "status,data,headers,error",
    [
        (200, {"ok": False, "error": "msg_too_long"}, {}, "msg_too_long"),
        (429, {"ok": False}, {"Retry-After": "30"}, "rate_limited: 30"),
        (429, {"ok": False}, {}, "rate_limited"),
        (200, {"ok": False, "error": "ratelimited"}, {}, "rate_limited"),
        (503, {"ok": False}, {}, "http_error: HTTPStatusError"),
        (200, "invalid JSON", {}, "invalid_slack_response"),
    ],
)
async def test_message_failures_preserve_tool_error_codes(slack_api, status, data, headers, error):
    slack_api.respond(data, status=status, headers=headers)
    assert await slack_utils.post_slack_thread_reply_with_ts("C1", "1.0", "hello") == (None, error)
    assert len(slack_api.calls) == 1


async def test_post_slack_thread_reply_with_ts_sends_blocks(slack_api, monkeypatch):
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "Pick"}}]
    assert await slack_utils.post_slack_thread_reply_with_ts(
        "C1", "1.0", "Pick", blocks=blocks, agent_thread_id="mapped-thread"
    ) == ("1.0", None)
    payload = slack_api.calls[0][1]
    footer = "<https://dashboard.example/agents/mapped-thread|Open in Web>"
    assert payload["text"] == f"Pick {footer}"
    assert payload["blocks"] == [
        *blocks,
        {"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]},
    ]
    assert payload["thread_ts"] == "1.0"


async def test_top_level_message_omits_thread_ts(slack_api):
    assert await slack_utils.post_slack_top_level_message_with_ts("C1", "hello") == ("1.0", None)
    assert slack_api.calls[0][1] == {
        "channel": "C1",
        "text": "hello",
        "unfurl_links": True,
        "unfurl_media": True,
    }


async def test_missing_token_does_not_make_request(slack_api, monkeypatch):
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "")
    assert await slack_utils.post_slack_thread_reply_with_ts("C1", "1.0", "hello") == (
        None,
        "missing_slack_bot_token",
    )
    assert slack_api.calls == []


async def test_bool_reply_reports_failure(slack_api):
    slack_api.respond({"ok": False, "error": "channel_not_found"})
    assert await slack_utils.post_slack_thread_reply("C1", "1.0", "hello") is False


async def test_upload_completes_external_upload_without_sending_token(slack_api, monkeypatch):
    slack_api.respond(
        {"ok": True, "upload_url": "https://files.slack.com/upload/v1/test", "file_id": "F1"}
    )
    slack_api.respond({"ok": True, "files": [{"id": "F1"}]})
    uploads = []

    def upload(request):
        uploads.append(request)
        return httpx2.Response(200, text="OK - 8")

    async_client = httpx2.AsyncClient
    with patch.object(
        httpx2,
        "AsyncClient",
        side_effect=lambda **kwargs: async_client(**kwargs, transport=httpx2.MockTransport(upload)),
    ):
        assert await slack_utils.upload_slack_thread_file(
            "C1", "1.0", "plan.html", b"<html />", title="Plan", initial_comment="Preview"
        ) == ("F1", None)
    assert slack_api.calls[0] == (
        "files.getUploadURLExternal",
        {"filename": "plan.html", "length": "8"},
    )
    assert slack_api.calls[1][0] == "files.completeUploadExternal"
    completion = slack_api.calls[1][1]
    assert json.loads(completion.pop("files")) == [{"id": "F1", "title": "Plan"}]
    assert completion == {"channel_id": "C1", "thread_ts": "1.0", "initial_comment": "Preview"}
    assert len(uploads) == 1
    assert uploads[0].content == b"<html />"
    assert "authorization" not in uploads[0].headers


@pytest.mark.parametrize("redirect", [False, True])
async def test_upload_blocks_unsafe_urls_and_redirects(slack_api, redirect):
    slack_api.respond(
        {
            "ok": True,
            "file_id": "F1",
            "upload_url": "https://files.slack.com/upload/v1/test"
            if redirect
            else "https://attacker.example/upload",
        }
    )
    uploads = []

    def upload(request):
        uploads.append(request)
        return httpx2.Response(307, headers={"Location": "https://attacker.example/upload"})

    async_client = httpx2.AsyncClient
    with patch.object(
        httpx2,
        "AsyncClient",
        side_effect=lambda **kwargs: async_client(**kwargs, transport=httpx2.MockTransport(upload)),
    ):
        assert await slack_utils.upload_slack_thread_file("C1", "1.0", "plan.html", b"x") == (
            None,
            "unsafe_upload_url",
        )
    assert len(slack_api.calls) == 1
    assert len(uploads) == int(redirect)


async def test_upload_rejects_large_content_without_request(slack_api):
    assert await slack_utils.upload_slack_thread_file(
        "C1", "1.0", "plan.html", b"x" * (16 * 1024 * 1024 + 1)
    ) == (None, "file_too_large")
    assert slack_api.calls == []


async def test_upload_handles_malformed_response(slack_api):
    slack_api.respond("invalid JSON")
    assert await slack_utils.upload_slack_thread_file("C1", "1.0", "plan.html", b"x") == (
        None,
        "invalid_slack_response",
    )
