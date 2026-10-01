"""Slack message behavior exercised through the SDK's real HTTP requests."""

import json
import logging
from unittest.mock import patch

import httpx2
import pytest

from agent.slack import client as slack_utils


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


async def test_slack_stream_rate_limit_preserves_retry_after(slack_api):
    slack_api.respond({"ok": False}, status=429, headers={"Retry-After": "30"})
    with pytest.raises(slack_utils.SlackStreamError) as raised:
        await slack_utils.append_slack_stream("C1", "1.0", [])
    assert raised.value.code == "rate_limited"
    assert raised.value.retry_after == 30
    assert len(slack_api.calls) == 1


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


async def test_reply_to_a_deleted_parent_is_removed_instead_of_left_at_the_channel_root(
    slack_api,
) -> None:
    slack_api.respond({"ok": True, "ts": "2.0", "message": {"text": "Answer"}})
    slack_api.respond({"ok": False, "error": "thread_not_found"})
    slack_api.respond({"ok": True})
    assert await slack_utils.post_slack_thread_reply_with_ts("C1", "1.0", "Answer") == (
        None,
        "thread_not_found",
    )
    assert [method for method, _ in slack_api.calls] == [
        "chat.postMessage",
        "conversations.replies",
        "chat.delete",
    ]
    assert slack_api.calls[2][1] == {"channel": "C1", "ts": "2.0"}


async def test_reply_is_kept_when_the_thread_still_exists(slack_api) -> None:
    slack_api.respond({"ok": True, "ts": "2.0", "message": {"text": "Answer"}})
    slack_api.respond({"ok": True, "messages": [{"ts": "1.0"}]})
    assert await slack_utils.post_slack_thread_reply_with_ts("C1", "1.0", "Answer") == ("2.0", None)
    assert [method for method, _ in slack_api.calls] == [
        "chat.postMessage",
        "conversations.replies",
    ]
