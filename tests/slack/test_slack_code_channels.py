import json
from typing import Any, cast
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from fastapi import BackgroundTasks
from starlette.requests import Request

from agent.slack import client as slack_utils
from agent.slack import code_channels as slack_code_channels
from agent.slack import events as slack_events
from agent.slack import routes as slack_routes
from agent.slack import webhook as slack_service
from agent.slack.request import SlackRequest
from agent.webhooks import common as webhook_common


class _FakeRequest:
    def __init__(self, payload: dict[str, Any] | bytes) -> None:
        self.headers: dict[str, str] = {}
        self._body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    async def body(self) -> bytes:
        return self._body


@pytest.fixture
def code_channel_route(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    process = AsyncMock()
    monkeypatch.setattr(webhook_common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(webhook_common, "is_code_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(slack_routes, "get_langgraph_client", lambda: object())
    monkeypatch.setattr(
        webhook_common, "lookup_slack_thread_id", AsyncMock(return_value="thread-1")
    )
    monkeypatch.setattr(webhook_common, "claim_slack_event", AsyncMock(return_value=True))
    monkeypatch.setattr(
        webhook_common,
        "resolve_slack_channel_context",
        AsyncMock(
            return_value={
                "name": "code-task",
                "is_ext_shared": False,
                "is_pending_ext_shared": False,
            }
        ),
    )
    monkeypatch.setattr(
        webhook_common,
        "get_slack_repo_config",
        AsyncMock(return_value={"owner": "langchain-ai", "name": "open-swe"}),
    )
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USER_ID", "BOT")
    monkeypatch.setattr(slack_routes.service, "process_slack_mention", process)
    monkeypatch.setattr(slack_routes, "_synthetic_slack_ts", lambda: "1786574000.000001")
    return process


async def test_runtime_slash_command_routes_to_code_channel_session(
    code_channel_route: AsyncMock,
) -> None:
    body = urlencode(
        {
            "channel_id": "C-code",
            "user_id": "U1",
            "command": "/run-tests",
            "text": "tests/slack",
            "trigger_id": "trigger-1",
        }
    ).encode()
    background_tasks = BackgroundTasks()

    response = await slack_routes.slack_code_channel_command(
        cast(Request, _FakeRequest(body)), background_tasks
    )
    await background_tasks()

    assert response == {"response_type": "ephemeral", "text": "Working on /run-tests…"}
    assert code_channel_route.await_args is not None
    request = cast(SlackRequest, code_channel_route.await_args.args[0])
    assert request.thread_ts == "0"
    assert request.explicit_request is True
    assert "/run-tests tests/slack" in request.text


async def test_context_bar_action_routes_to_code_channel_session(
    code_channel_route: AsyncMock,
) -> None:
    background_tasks = BackgroundTasks()
    response = await slack_routes.slack_webhook(
        cast(
            Request,
            _FakeRequest(
                {
                    "type": "event_callback",
                    "event_id": "EvAction",
                    "event": {
                        "type": "code_channel_action",
                        "channel": "C-code",
                        "user": "U1",
                        "event_ts": "1786574000.000002",
                        "action": {"key": "create-pr", "label": "Create PR"},
                    },
                }
            ),
        ),
        background_tasks,
    )
    await background_tasks()

    assert response["status"] == "accepted"
    assert code_channel_route.await_args is not None
    assert "create-pr" in code_channel_route.await_args.args[0].text


@pytest.mark.parametrize("is_private", [False, True])
async def test_create_code_channel_sets_visibility(
    monkeypatch: pytest.MonkeyPatch, is_private: bool
) -> None:
    call = AsyncMock(return_value=({"channel": {"id": "C-code"}}, None))
    monkeypatch.setattr(slack_code_channels, "_call", call)

    channel_id, error = await slack_code_channels.create_code_channel(
        name="Fix flaky tests",
        session_id="thread-1",
        origin_channel_id="C-origin",
        origin_message_ts="1.000",
        is_private=is_private,
    )

    assert (channel_id, error) == ("C-code", None)
    assert call.await_args_list[0].args[1]["is_private"] is is_private


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

    async def channel_context(_channel_id: str, *, use_cache: bool = True) -> dict[str, Any]:
        return {"is_ext_shared": False, "is_pending_ext_shared": False}

    monkeypatch.setattr(webhook_common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(webhook_common, "claim_slack_event", AsyncMock(return_value=True))
    monkeypatch.setattr(webhook_common, "is_code_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(webhook_common, "resolve_slack_thread_id", AsyncMock(return_value="t1"))
    monkeypatch.setattr(webhook_common, "thread_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(webhook_common, "resolve_slack_channel_context", channel_context)
    monkeypatch.setattr(
        webhook_common,
        "get_slack_repo_config",
        AsyncMock(return_value={"owner": "langchain-ai", "name": "open-swe"}),
    )
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
    request = cast(SlackRequest, background_tasks.tasks[0].args[0])
    assert request.code_channel is True
    assert request.treat_all_messages_as_mentions is True
    assert request.thread_ts == webhook_common.CODE_CHANNEL_SESSION_TS
    assert request.reply_thread_ts == "1786573300.000000"


async def test_code_channel_replies_are_posted_top_level(slack_api):
    assert await slack_utils._post_slack_message_with_ts("C-code", "done", thread_ts="0") == (
        "1.0",
        None,
    )
    assert "thread_ts" not in slack_api.calls[0][1]


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["U06KD8BFY95"], ["U06KD8BFY95"]),
        (["<@U1>", "<@U2|ramon>"], ["U1", "U2"]),
        (["u1", "U1", " U1 "], ["U1"]),
        (["W123"], ["W123"]),
        (["", "not-an-id", "@ramon", "C123"], []),
    ],
)
def test_slack_user_ids_reads_ids_however_they_were_written(
    values: list[str], expected: list[str]
) -> None:
    assert slack_utils.slack_user_ids(values) == expected


@pytest.fixture
def invite_call(slack_api) -> dict[str, Any]:
    captured: dict[str, Any] = {"payload": None, "response": {"ok": True}}

    def handle(method, params, headers):
        assert method == "conversations.invite"
        captured["payload"] = params
        return 200, captured["response"], {}

    slack_api.handler = handle
    return captured


async def test_an_invite_forces_past_the_ids_slack_refuses(invite_call: dict[str, Any]) -> None:
    """Without `force` Slack drops the whole batch when one user fails."""
    invited, error = await slack_utils.invite_to_slack_channel("C1", ["U1", "U2"])

    assert invite_call["payload"] == {"channel": "C1", "users": "U1,U2", "force": "1"}
    assert (invited, error) == (["U1", "U2"], "")


async def test_a_stale_id_costs_only_itself(invite_call: dict[str, Any]) -> None:
    invite_call["response"] = {
        "ok": True,
        "errors": [{"user": "U2", "ok": False, "error": "user_not_found"}],
    }

    invited, error = await slack_utils.invite_to_slack_channel("C1", ["U1", "U2", "U3"])

    assert invited == ["U1", "U3"]
    assert error == "U2 (user_not_found)"


async def test_someone_already_in_the_channel_counts_as_invited(
    invite_call: dict[str, Any],
) -> None:
    invite_call["response"] = {
        "ok": True,
        "errors": [{"user": "U1", "ok": False, "error": "already_in_channel"}],
    }

    assert await slack_utils.invite_to_slack_channel("C1", ["U1"]) == (["U1"], "")


async def test_a_refused_call_names_everyone_it_could_not_invite(
    invite_call: dict[str, Any],
) -> None:
    invite_call["response"] = {"ok": False, "error": "missing_scope"}

    assert await slack_utils.invite_to_slack_channel("C1", ["U1", "U2"]) == (
        [],
        "U1, U2: missing_scope",
    )


async def test_no_usable_ids_never_reaches_slack(invite_call: dict[str, Any]) -> None:
    assert await slack_utils.invite_to_slack_channel("C1", ["nope"]) == ([], "no_users")
    assert invite_call["payload"] is None
