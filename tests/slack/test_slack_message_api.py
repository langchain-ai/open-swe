"""Slack message behavior exercised through the SDK's real HTTP requests."""

import json
import logging
from unittest.mock import AsyncMock, patch

import httpx2
import pytest

from agent.dashboard.workspace_settings import WorkspaceSettingsUpdate, upsert_instance_settings
from agent.slack import client as slack_utils
from agent.slack import pr_links
from agent.slack.blocks import actions, block_payload, button, code_blocks, markdown, section
from agent.tools.manage_feature_flags import manage_feature_flags
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


@pytest.mark.parametrize("delivery", ["post", "update", "ephemeral", "command"])
async def test_review_link_flag_changes_displayed_links_not_code_or_button_values(
    fake_store: FakeStore,
    slack_api: SlackAPI,
    monkeypatch: pytest.MonkeyPatch,
    delivery: str,
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://openswe.example/prefix/")
    monkeypatch.setattr(pr_links, "workspace_for_slack_channel", AsyncMock(return_value="team"))
    fake_store.seed(["workspace_settings"], "team", {"pr_review_links": True})
    await upsert_instance_settings(WorkspaceSettingsUpdate(pr_review_links=delivery == "command"))
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
        assert await slack_utils.post_slack_top_level_message_with_ts(
            "C1", text, blocks=blocks
        ) == (
            "1.0",
            None,
        )
    elif delivery == "update":
        assert await slack_utils.update_slack_message("C1", "1.0", text, blocks=blocks) == (
            True,
            None,
        )
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


async def test_review_links_default_off_inherit_and_allow_workspace_opt_out(
    fake_store: FakeStore,
    slack_api: SlackAPI,
    monkeypatch: pytest.MonkeyPatch,
    grant_tool_access,
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://openswe.example")
    monkeypatch.setattr(pr_links, "workspace_for_slack_channel", AsyncMock(return_value="team"))
    grant_tool_access(admin=True, admin_surface=True)
    url = "https://github.com/acme/app/pull/7"
    await slack_utils.post_slack_top_level_message_with_ts("C1", url)
    assert slack_api.calls[-1][1]["text"] == url
    await manage_feature_flags("set", {"pr_review_links": True})
    await slack_utils.post_slack_top_level_message_with_ts("C1", url)
    assert slack_api.calls[-1][1]["text"] == "https://openswe.example/agents/reviews/acme/app/7"
    fake_store.seed(["workspace_settings"], "team", {"pr_review_links": False})
    await slack_utils.post_slack_top_level_message_with_ts("C1", url)
    assert slack_api.calls[-1][1]["text"] == url
    fake_store.seed(["workspace_settings"], "team", {})
    monkeypatch.delenv("DASHBOARD_BASE_URL")
    await slack_utils.post_slack_top_level_message_with_ts("C1", url)
    assert slack_api.calls[-1][1]["text"] == url
