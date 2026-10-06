"""Slack message behavior exercised through the SDK's real HTTP requests."""

import json
import logging
from unittest.mock import AsyncMock, patch

import httpx2
import pytest

from agent.dashboard.profiles import ProfileUpdate, put_my_profile
from agent.run_config import RunConfig
from agent.slack import client as slack_utils
from agent.slack.blocks import actions, block_payload, button, code_blocks, markdown, section
from agent.users import User, UserPreferences
from agent.utils import url_safety
from tests.conftest import FakeStore
from tests.support.slack_api import SlackAPI


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


@pytest.mark.parametrize("status", ["Thinking…", ""])
async def test_concierge_status_does_not_create_slack_threads(slack_api: SlackAPI, status: str):
    assert await slack_utils.set_slack_thread_status("D1", "0", status)
    assert slack_api.calls == []


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
    monkeypatch.setattr(
        url_safety.socket, "getaddrinfo", lambda *args: [(None, None, None, None, ("1.1.1.1", 0))]
    )
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
        assert (
            await slack_utils.upload_slack_thread_file(
                "C1", "1.0", "plan.html", b"<html />", title="Plan", initial_comment="Preview"
            )
            == "F1"
        )
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
async def test_upload_blocks_unsafe_urls_and_redirects(slack_api, redirect, monkeypatch):
    slack_api.respond(
        {
            "ok": True,
            "file_id": "F1",
            "upload_url": "https://files.slack.com/upload/v1/test"
            if redirect
            else "https://attacker.example/upload",
        }
    )
    monkeypatch.setattr(
        url_safety.socket, "getaddrinfo", lambda *args: [(None, None, None, None, ("1.1.1.1", 0))]
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
        with pytest.raises(slack_utils.SlackRequestError, match="unsafe_upload_url"):
            await slack_utils.upload_slack_thread_file("C1", "1.0", "plan.html", b"x")
    assert len(slack_api.calls) == 1
    assert len(uploads) == int(redirect)


async def test_reply_to_a_deleted_parent_is_removed_instead_of_left_at_the_channel_root(
    slack_api,
) -> None:
    slack_api.respond({"ok": True, "ts": "2.0", "message": {"text": "Answer"}})
    slack_api.respond({"ok": False, "error": "thread_not_found"})
    slack_api.respond({"ok": True})
    with pytest.raises(slack_utils.SlackRequestError, match="thread_not_found"):
        await slack_utils.post_slack_thread_reply_with_ts("C1", "1.0", "Answer")
    assert [method for method, _ in slack_api.calls] == [
        "chat.postMessage",
        "conversations.replies",
        "chat.delete",
    ]
    assert slack_api.calls[2][1] == {"channel": "C1", "ts": "2.0"}


async def test_reply_is_kept_when_the_thread_still_exists(slack_api) -> None:
    slack_api.respond({"ok": True, "ts": "2.0", "message": {"text": "Answer"}})
    slack_api.respond({"ok": True, "messages": [{"ts": "1.0"}]})
    assert await slack_utils.post_slack_thread_reply_with_ts("C1", "1.0", "Answer") == "2.0"
    assert [method for method, _ in slack_api.calls] == [
        "chat.postMessage",
        "conversations.replies",
    ]


@pytest.mark.parametrize("delivery", ["post", "update", "ephemeral", "command"])
async def test_review_link_flag_changes_displayed_links_not_code_or_button_values(
    slack_api: SlackAPI,
    monkeypatch: pytest.MonkeyPatch,
    delivery: str,
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://openswe.example/prefix/")
    monkeypatch.setattr(RunConfig, "from_runtime", lambda: RunConfig(github_login="alice"))
    monkeypatch.setattr(
        User, "preferences_for_login", AsyncMock(return_value=UserPreferences(pr_review_links=True))
    )
    url = "https://github.com/acme/app/pull/7"
    target = "https://openswe.example/prefix/agents/reviews/acme/app/7"
    unchanged = f"{url}/files {url}?diff=split {url}#discussion https://example.com/acme/app/pull/7"
    text = f"[PR]({url}), <{url}|PR> and {url}.\n`{url}`\n```bash\n{url}\n```\n{unchanged}"
    blocks = block_payload(
        [
            markdown(text),
            section(f"<{url}|PR>"),
            actions(button("I'll review", action_id="review", value=url, url=url)),
            *code_blocks(url),
        ]
    )
    if delivery == "post":
        assert (
            await slack_utils.post_slack_top_level_message_with_ts("C1", text, blocks=blocks)
            == "1.0"
        )
    elif delivery == "update":
        await slack_utils.update_slack_message("C1", "1.0", text, blocks=blocks)
    elif delivery == "ephemeral":
        assert await slack_utils.post_slack_ephemeral_message("C1", "U1", text, blocks=blocks)
    else:

        async def handle(request: httpx2.Request) -> httpx2.Response:
            slack_api.calls.append(("callback", json.loads(request.content)))
            return httpx2.Response(200, text="ok")

        with patch.object(
            slack_utils.httpx2, "AsyncHTTPTransport", return_value=httpx2.MockTransport(handle)
        ):
            assert await slack_utils.replace_slack_command_message(
                "https://hooks.slack.com/commands/test", text, blocks=blocks
            )
    payload = slack_api.calls[0][1]
    expected = (
        f"[PR]({target}), <{target}|PR> and {target}.\n`{url}`\n```bash\n{url}\n```\n{unchanged}"
    )
    assert payload["text"] == expected
    sent_blocks = payload["blocks"]
    assert sent_blocks[0]["text"] == expected
    assert sent_blocks[1]["text"]["text"] == f"<{target}|PR>"
    assert sent_blocks[2]["elements"][0]["url"] == target
    assert sent_blocks[2]["elements"][0]["value"] == url
    assert sent_blocks[3] == blocks[3]
    assert blocks[0]["text"] == text


@pytest.mark.usefixtures("registry_db")
async def test_review_links_are_opt_in_per_user_even_in_the_same_channel(
    fake_store: FakeStore,
    slack_api: SlackAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://openswe.example")
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice,bob")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")
    await User.sign_in("github", "1", login="alice")
    await User.sign_in("github", "2", login="bob")
    cfg = RunConfig(github_login="alice")
    monkeypatch.setattr(RunConfig, "from_runtime", lambda: cfg)
    url = "https://github.com/acme/app/pull/7"
    await slack_utils.post_slack_top_level_message_with_ts("C1", url)
    assert slack_api.calls[-1][1]["text"] == url
    saved = await put_my_profile(
        ProfileUpdate(
            default_model="openai:gpt-6.1-sol", reasoning_effort="high", pr_review_links=True
        ),
        {"sub": "alice", "email": "alice@example.com"},
    )
    assert saved["pr_review_links"] is True
    await slack_utils.post_slack_top_level_message_with_ts("C1", url)
    assert slack_api.calls[-1][1]["text"] == "https://openswe.example/agents/reviews/acme/app/7"
    cfg.github_login = "bob"
    await slack_utils.post_slack_top_level_message_with_ts("C1", url)
    assert slack_api.calls[-1][1]["text"] == url
    await slack_utils.update_slack_message("C1", "1.0", url, login="alice")
    assert slack_api.calls[-1][1]["text"] == "https://openswe.example/agents/reviews/acme/app/7"
    cfg.github_login = "alice"
    monkeypatch.delenv("DASHBOARD_BASE_URL")
    await slack_utils.post_slack_top_level_message_with_ts("C1", url)
    assert slack_api.calls[-1][1]["text"] == url
