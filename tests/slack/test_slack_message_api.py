"""Slack message behavior exercised through the SDK's real HTTP requests."""

import importlib
import json
import logging
from unittest.mock import AsyncMock, patch

import httpx2
import pytest

from openswe import completion
from openswe.dashboard.profiles import ProfileUpdate, put_my_profile
from openswe.run_config import RunConfig
from openswe.slack import client as slack_utils
from openswe.slack.blocks import actions, block_payload, button, code_blocks, markdown, section
from openswe.users import User, UserPreferences
from openswe.utils import url_safety
from tests.support.slack_api import SlackAPI


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["commands", "actions"])
async def test_question_callback_delivery_preserves_shortcut_source(prefix: str) -> None:
    sent: list[dict[str, object]] = []

    async def handle(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, text="ok")

    url = f"https://hooks.slack.com/{prefix}/T1/B1/test-response"
    with patch.object(
        slack_utils.httpx2, "AsyncHTTPTransport", return_value=httpx2.MockTransport(handle)
    ):
        assert await slack_utils.acknowledge_slack_command(url, "Working")
        assert await slack_utils.replace_slack_command_message(url, "Answer")
        assert await slack_utils.clear_slack_command_message(url)
    assert sent[0] == {"response_type": "ephemeral", "text": "Working"}
    assert sent[1]["response_type"] == "ephemeral"
    assert sent[1]["replace_original"] is (prefix == "commands")
    if prefix == "actions":
        assert len(sent) == 2
    else:
        assert sent[2] == {"delete_original": True}


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


async def test_thread_status_uses_agent_session_and_falls_back_without_agent_view(slack_api):
    slack_api.respond({"ok": True})
    slack_api.respond({"ok": False, "error": "not_allowed"})
    assert await slack_utils.set_slack_thread_status("C1", "1.0", "Thinking…")
    assert await slack_utils.set_slack_thread_status("C1", "1.0", "")
    assert slack_api.calls == [
        (method, {"channel_id": "C1", "thread_ts": "1.0", "status": status})
        for method, status in [
            ("agents.sessions.setStatus", "processing"),
            ("agents.sessions.setStatus", "active"),
            ("assistant.threads.setStatus", ""),
        ]
    ]
    slack_api.calls.clear()
    slack_api.respond({"ok": False, "error": "internal_error"})
    assert not await slack_utils.set_slack_thread_status("C1", "1.0", "")
    assert [method for method, _ in slack_api.calls] == ["agents.sessions.setStatus"]


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
        side_effect=lambda **kwargs: async_client(
            **{**kwargs, "transport": httpx2.MockTransport(upload)}
        ),
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
        side_effect=lambda **kwargs: async_client(
            **{**kwargs, "transport": httpx2.MockTransport(upload)}
        ),
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


@pytest.mark.parametrize(
    ("status", "reason_code"),
    [("error", "provider_rate_limited"), ("error", None), ("timeout", None)],
)
async def test_a_notice_is_not_repeated_while_the_thread_still_ends_with_it(
    slack_api: SlackAPI,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    reason_code: str | None,
) -> None:
    thread: list[dict[str, object]] = [{"ts": "1.0", "text": "Fix it"}]

    def handle(method: str, params: dict[str, object], _headers: object):
        if method == "chat.postMessage":
            blocks = params.get("blocks")
            message = {
                "ts": f"{len(thread) + 1}.0",
                "text": params["text"],
                "blocks": json.loads(blocks) if isinstance(blocks, str) else blocks or [],
            }
            thread.append(message)
            return 200, {"ok": True, "ts": message["ts"], "message": message}, {}
        parent = thread[0] | {"latest_reply": thread[-1]["ts"]}
        found = [m for m in [parent, *thread[1:]] if m["ts"] == params.get("oldest", "1.0")]
        return 200, {"ok": True, "messages": found}, {}

    slack_api.handler = handle
    monkeypatch.setattr(completion, "get_langsmith_error_url", AsyncMock(return_value=None))
    metadata = {
        "source": "slack",
        "source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}},
    }
    sandbox = slack_utils.SlackNotice("sandbox_unreachable", "Sandbox stopped responding")

    for _ in range(2):
        assert await slack_utils.post_slack_thread_reply("C1", "1.0", sandbox.key, notice=sandbox)
        assert await completion._post_failure_reply(
            "t1", metadata, "error", reason_code="sandbox_unreachable"
        )
    assert [message["text"] for message in thread] == ["Fix it", sandbox.key]

    for _ in range(2):
        assert await completion._post_failure_reply("t1", metadata, status, reason_code)
    thread.append({"ts": "9.0", "text": "@Open SWE stop please"})
    assert await completion._post_failure_reply("t1", metadata, status, reason_code)

    failure = completion._failure_text(status, reason_code=reason_code)
    assert [message["text"] for message in thread] == [
        "Fix it",
        sandbox.key,
        failure,
        "@Open SWE stop please",
        failure,
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


@pytest.mark.parametrize("fail", [False, True])
async def test_direct_github_reply_override_is_scoped_to_one_delivery(
    slack_api: SlackAPI, monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    reply = importlib.import_module("openswe.slack.tools.reply")
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://openswe.example")
    monkeypatch.setattr(RunConfig, "from_runtime", lambda: RunConfig(github_login="alice"))
    monkeypatch.setattr(
        User, "preferences_for_login", AsyncMock(return_value=UserPreferences(pr_review_links=True))
    )
    url = "https://github.com/acme/app/pull/7"
    blocks = block_payload([markdown(f"[GitHub]({url})"), section(f"<{url}|GitHub>")])

    async def send(message: str, *_args: object, **_kwargs: object) -> dict[str, object]:
        await slack_utils.post_slack_top_level_message_with_ts("C1", message, blocks=blocks)
        if fail:
            raise RuntimeError("delivery failed")
        return {"success": True}

    monkeypatch.setattr(reply, "send_slack_reply", send)
    if fail:
        with pytest.raises(RuntimeError, match="delivery failed"):
            await reply.slack_reply(url, "final", preserve_github_links=True)
    else:
        assert await reply.slack_reply(url, "final", preserve_github_links=True) == {
            "success": True
        }
    assert slack_api.calls[-1][1]["text"] == url
    assert slack_api.calls[-1][1]["blocks"] == blocks
    await slack_utils.post_slack_top_level_message_with_ts("C1", url)
    assert slack_api.calls[-1][1]["text"] == "https://openswe.example/agents/reviews/acme/app/7"


@pytest.mark.usefixtures("registry_db")
async def test_review_links_are_opt_in_per_user_even_in_the_same_channel(
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
