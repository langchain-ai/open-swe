import json
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import BackgroundTasks
from starlette.requests import Request

from agent.utils import slack as slack_utils
from agent.utils import slack_code_channels, slack_events
from agent.webhooks import common as webhook_common
from agent.webhooks import slack as slack_service
from agent.webhooks import slack_routes


class _FakeRequest:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.headers: dict[str, str] = {}
        self._body = json.dumps(payload).encode()

    async def body(self) -> bytes:
        return self._body


async def test_set_diff_view_uses_documented_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = AsyncMock(return_value=({"ok": True}, None))
    monkeypatch.setattr(slack_code_channels, "_call", call)

    ok, error = await slack_code_channels.set_diff_view(
        "C-code",
        "diff --git a/a.py b/a.py\n-old\n+new\n",
        base_branch="main",
        head_branch="feature/code-channels",
    )

    assert ok is True
    assert error is None
    call.assert_awaited_once_with(
        "agents.conversations.setView",
        {
            "channel_id": "C-code",
            "type": "diff",
            "content": "diff --git a/a.py b/a.py\n-old\n+new\n",
            "base_branch": "main",
            "head_branch": "feature/code-channels",
        },
    )


async def test_set_view_rejects_content_over_one_megabyte(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = AsyncMock()
    monkeypatch.setattr(slack_code_channels, "_call", call)

    data, error = await slack_code_channels.set_view(
        "C-code",
        "html",
        view_key="report",
        content="é" * (slack_code_channels.VIEW_CONTENT_MAX_BYTES // 2 + 1),
    )

    assert data is None
    assert error == "content_too_large"
    call.assert_not_awaited()


async def test_set_view_supports_html_block_kit_and_canvas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = AsyncMock(return_value=({"ok": True, "view_id": "V1"}, None))
    monkeypatch.setattr(slack_code_channels, "_call", call)

    await slack_code_channels.set_view(
        "C-code",
        "html",
        view_key="reports/coverage.html",
        name="Coverage",
        content="<!doctype html><title>Coverage</title>",
        csp={"resource_domains": ["https://cdn.jsdelivr.net"]},
    )
    await slack_code_channels.set_view(
        "C-code",
        "block_kit",
        view_key="plan",
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "Plan"}}],
    )
    await slack_code_channels.set_view(
        "C-code",
        "canvas",
        view_key="design-doc",
        canvas_id="F123",
        access_level="comment",
    )

    assert call.await_args_list[0].args == (
        "agents.conversations.setView",
        {
            "channel_id": "C-code",
            "type": "html",
            "content": "<!doctype html><title>Coverage</title>",
            "view_key": "reports/coverage.html",
            "name": "Coverage",
            "csp": {"resource_domains": ["https://cdn.jsdelivr.net"]},
        },
    )
    assert call.await_args_list[1].args[1]["blocks"][0]["type"] == "section"
    assert call.await_args_list[2].args[1] == {
        "channel_id": "C-code",
        "type": "canvas",
        "canvas_id": "F123",
        "access_level": "comment",
        "view_key": "design-doc",
    }


async def test_commands_properties_and_canvas_methods_use_canonical_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = AsyncMock(
        side_effect=[
            ({"ok": True, "command_count": 1}, None),
            ({"ok": True}, None),
            ({"ok": True, "views": [{"view_id": "V1", "type": "html"}]}, None),
            ({"ok": True, "view_id": "V1"}, None),
            ({"ok": True, "canvas_id": "F1", "content": "# Plan"}, None),
            ({"ok": True, "sections_changed_count": 1}, None),
        ]
    )
    monkeypatch.setattr(slack_code_channels, "_call", call)

    commands = [{"name": "create-pr", "description": "Open a pull request"}]
    items = [{"key": "review", "label": "Review", "item_type": "action"}]
    await slack_code_channels.set_commands("C-code", commands)
    await slack_code_channels.set_properties(
        "C-code",
        code_channel={"context_bar_items": items},
        agent_resource={"url": "https://example.com", "resource_type": "ticket"},
    )
    views, error = await slack_code_channels.list_views("C-code")
    await slack_code_channels.remove_view("C-code", view_id="V1")
    await slack_code_channels.get_canvas("C-code", "F1", include_resolved=True)
    await slack_code_channels.set_canvas_content("C-code", "F1", "# Revised")

    assert views == [{"view_id": "V1", "type": "html"}]
    assert error is None
    assert call.await_args_list[0].args[1] == {"channel_id": "C-code", "commands": commands}
    assert call.await_args_list[1].args[1] == {
        "channel_id": "C-code",
        "code_channel": {"context_bar_items": items},
        "agent_resource": {"url": "https://example.com", "resource_type": "ticket"},
    }
    assert call.await_args_list[3].args[1] == {"channel_id": "C-code", "view_id": "V1"}
    assert call.await_args_list[4].args[1] == {
        "channel_id": "C-code",
        "canvas_id": "F1",
        "content_format": "markdown",
        "include_resolved": True,
    }
    assert call.await_args_list[5].args[1] == {
        "channel_id": "C-code",
        "canvas_id": "F1",
        "content": "# Revised",
    }


def test_repo_context_bar_items_use_supported_icons_and_branch_link() -> None:
    items = slack_code_channels.repo_context_bar_items(
        {"owner": "langchain-ai", "name": "open-swe"},
        branch="feature/code channels",
        pr_url="https://github.com/langchain-ai/open-swe/pull/2252",
    )

    assert items[1] == {
        "key": "branch",
        "label": "feature/code channels",
        "icon": "branch",
        "url": "https://github.com/langchain-ai/open-swe/tree/feature/code%20channels",
    }


def test_untagged_code_channel_message_does_not_interrupt_active_work() -> None:
    assert not slack_service._interrupts_active_run(
        "talking to a teammate",
        "BOT",
        treat_all_messages_as_mentions=True,
        code_channel=True,
        message_update=False,
        explicit_request=False,
    )
    assert slack_service._interrupts_active_run(
        "<@BOT> stop and do this",
        "BOT",
        treat_all_messages_as_mentions=True,
        code_channel=True,
        message_update=False,
        explicit_request=False,
    )
    assert slack_service._interrupts_active_run(
        "/run-tests",
        "BOT",
        treat_all_messages_as_mentions=True,
        code_channel=True,
        message_update=False,
        explicit_request=True,
    )


async def test_untagged_code_channel_message_routes_to_the_channel_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slack_events.reset_slack_event_claims()

    async def channel_context(_channel_id: str) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(webhook_common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(webhook_common, "is_code_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(webhook_common, "resolve_slack_thread_id", AsyncMock(return_value="t1"))
    monkeypatch.setattr(webhook_common, "_thread_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(webhook_common, "_get_slack_channel_context", channel_context)
    monkeypatch.setattr(webhook_common, "_is_docs_plz_slack_channel", AsyncMock(return_value=False))
    monkeypatch.setattr(
        webhook_common,
        "get_slack_repo_config",
        AsyncMock(return_value={"owner": "langchain-ai", "name": "open-swe"}),
    )
    monkeypatch.setattr(webhook_common, "increment_slack_thread_version", AsyncMock(return_value=1))
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USER_ID", "BOT")

    background_tasks = BackgroundTasks()
    response = await slack_routes.slack_webhook(
        cast(
            Request,
            _FakeRequest(
                {
                    "type": "event_callback",
                    "event_id": "Ev-code-channel",
                    "authorizations": [{"user_id": "BOT"}],
                    "event": {
                        "type": "message",
                        "channel": "C-code",
                        "ts": "1786573369.551099",
                        "thread_ts": "1786573300.000000",
                        "user": "U1",
                        "text": "no mention needed here",
                    },
                }
            ),
        ),
        background_tasks,
    )

    assert response["status"] == "accepted", response
    event_data = cast(dict[str, Any], background_tasks.tasks[0].args[0])
    assert event_data["code_channel"] is True
    assert event_data["treat_all_messages_as_mentions"] is True
    assert event_data["thread_ts"] == webhook_common.CODE_CHANNEL_SESSION_TS
    assert event_data["reply_thread_ts"] == "1786573300.000000"


async def test_code_channel_replies_are_posted_top_level(monkeypatch: pytest.MonkeyPatch) -> None:
    response = MagicMock(status_code=200)
    response.json.return_value = {"ok": True, "ts": "1786573400.000000"}
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.return_value = response

    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(slack_utils.httpx, "AsyncClient", lambda **_kwargs: client)

    await slack_utils._post_slack_message_with_ts(
        "C-code", "done", thread_ts=webhook_common.CODE_CHANNEL_SESSION_TS
    )

    assert "thread_ts" not in client.post.await_args.kwargs["json"]
