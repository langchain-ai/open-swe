"""Route-level coverage for Slack mention requirements and message edits."""

import json
from typing import Any, Literal, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import select
from starlette.datastructures import URL
from starlette.requests import Request

from openswe.database import postgres
from openswe.slack import events as slack_events
from openswe.slack import failures as slack_failures
from openswe.slack import routes as slack_routes
from openswe.slack import webhook as slack_service
from openswe.slack.payloads import SlackChannelContext
from openswe.slack.pr_links import SlackPullRequestLink
from openswe.slack.request import SlackRequest
from openswe.webhooks import common as webhook_common


class _FakeThreads:
    """Every claim succeeds — dedupe is covered in test_slack_event_dedupe.py."""

    async def create(self, *, thread_id: str, **_kwargs: Any) -> None:
        return None

    async def get(self, thread_id: str) -> dict[str, str]:
        raise KeyError(thread_id)


class _FakeClient:
    def __init__(self) -> None:
        self.threads = _FakeThreads()


class _FakeBackgroundTasks:
    def __init__(self) -> None:
        self.tasks: list[tuple[Any, tuple[Any, ...]]] = []

    def add_task(self, func: Any, *args: Any) -> None:
        self.tasks.append((func, args))


class _FakeRequest:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.headers: dict[str, str] = {}
        self.url = URL("http://test/webhooks/slack")
        self._body = json.dumps(payload).encode()

    async def body(self) -> bytes:
        return self._body


def _message_payload(text: str, event_id: str) -> dict[str, Any]:
    return {
        "type": "event_callback",
        "event_id": event_id,
        "authorizations": [{"user_id": "BOT"}],
        "event": {
            "type": "message",
            "channel": "C1",
            "ts": "1786573369.551099",
            "thread_ts": "1786573300.000000",
            "user": "U1",
            "text": text,
        },
    }


def _message_update_payload(*, bot_message: bool = False) -> dict[str, Any]:
    updated_message: dict[str, Any] = {
        "type": "message",
        "user": "BOT" if bot_message else "U1",
        "text": "new corrected text",
        "ts": "1786573369.551099",
        "thread_ts": "1786573300.000000",
    }
    if bot_message:
        updated_message["bot_id"] = "B1"
    return {
        "type": "event_callback",
        "event_id": "Ev-update",
        "authorizations": [{"user_id": "BOT"}],
        "event": {
            "type": "message",
            "subtype": "message_changed",
            "channel": "C1",
            "event_ts": "1786573400.000000",
            "ts": "1786573400.000000",
            "message": updated_message,
            "previous_message": {
                "type": "message",
                "user": "BOT" if bot_message else "U1",
                "text": "old text that must not be resent",
                "ts": "1786573369.551099",
                "thread_ts": "1786573300.000000",
            },
        },
    }


@pytest.fixture(autouse=True)
def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
    slack_events.reset_slack_event_claims()
    monkeypatch.setattr(slack_routes, "allow_solo_thread_followup", AsyncMock(return_value=False))
    monkeypatch.setattr(slack_routes, "is_kitchen_channel", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "openswe.incidents.channels.handle_slack_event", AsyncMock(return_value=None)
    )

    async def channel_context(_channel_id: str, *, use_cache: bool = True) -> SlackChannelContext:
        return SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=False)

    async def repo_config(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"owner": "langchain-ai", "name": "open-swe"}

    monkeypatch.setattr(slack_events, "get_client", lambda url: _FakeClient())
    monkeypatch.setattr(webhook_common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(webhook_common, "resolve_slack_thread_id", AsyncMock(return_value="t1"))
    monkeypatch.setattr(webhook_common, "lookup_slack_thread_id", AsyncMock(return_value="t1"))
    monkeypatch.setattr(
        webhook_common,
        "lookup_slack_run_mapping",
        AsyncMock(
            return_value={
                "run_id": "run-1",
                "thread_ts": "1786573300.000000",
                "triggering_user_id": "U1",
                "agent_thread_id": "t1",
            }
        ),
    )
    monkeypatch.setattr(webhook_common, "thread_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(webhook_common, "resolve_slack_channel_context", channel_context)
    monkeypatch.setattr(webhook_common, "get_slack_repo_config", repo_config)
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USER_ID", "BOT")
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USERNAME", "openswe")


@pytest.mark.parametrize(
    ("event_type", "text", "expected_status"),
    [
        ("message", "please ask @openswe about this", "ignored"),
        ("message", "/model:perf do it", "ignored"),
        ("message", "/btw why?", "ignored"),
        ("message", "/breakout fix it", "ignored"),
        ("message", "<@BOT> help", "accepted"),
        ("app_mention", "help", "accepted"),
    ],
)
async def test_only_slack_mentions_trigger_normal_channels(
    event_type: str, text: str, expected_status: str
) -> None:
    payload = _message_payload(text, "Ev-mention")
    payload["event"]["type"] = event_type
    background_tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(payload)), cast(BackgroundTasks, background_tasks)
    )

    assert response["status"] == expected_status
    assert bool(background_tasks.tasks) == (expected_status == "accepted")


async def test_a_watched_channel_message_checks_automations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_routes, "_slack_channel_watched", AsyncMock(return_value=True))
    background_tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_message_payload("deploy failed", "Ev-watched"))),
        cast(BackgroundTasks, background_tasks),
    )

    # Ignored as a conversation, still checked against Slack automations.
    assert response["status"] == "ignored"
    assert [task for task, _ in background_tasks.tasks] == [slack_routes._launch_slack_automations]


@pytest.mark.parametrize("reply", [False, True])
@pytest.mark.parametrize("event_type", ["message", "app_mention"])
async def test_kitchen_messages_preserve_explicit_mentions(
    monkeypatch: pytest.MonkeyPatch, reply: bool, event_type: str
) -> None:
    async def channel_context(_channel_id: str, *, use_cache: bool = True) -> SlackChannelContext:
        return SlackChannelContext(
            name="team-kitchen", is_ext_shared=False, is_pending_ext_shared=False
        )

    monkeypatch.setattr(webhook_common, "resolve_slack_channel_context", channel_context)
    monkeypatch.setattr(slack_routes, "is_kitchen_channel", AsyncMock(return_value=True))
    payload = _message_payload("please fix this", f"Ev-kitchen-{reply}-{event_type}")
    payload["event"]["type"] = event_type
    if not reply:
        del payload["event"]["thread_ts"]
    background_tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(payload)), cast(BackgroundTasks, background_tasks)
    )

    assert response["status"] == "accepted"
    request = cast(SlackRequest, background_tasks.tasks[0][1][0])
    assert request.thread_ts == ("1786573300.000000" if reply else "1786573369.551099")
    assert request.treat_all_messages_as_mentions is (event_type == "message")
    assert request.kitchen_channel is (event_type == "message")
    assert request.explicit_mention is (event_type == "app_mention")


@pytest.mark.parametrize("kitchen", [False, True])
@pytest.mark.parametrize(
    "text",
    [
        "<@OTHER> shots fired",
        "  <@OTHER> shots fired",
        "<@OTHER> ask <@BOT> later",
        "<@OTHER> /model:perf do it",
    ],
)
async def test_leading_other_user_mention_does_not_trigger(
    monkeypatch: pytest.MonkeyPatch, kitchen: bool, text: str
) -> None:
    monkeypatch.setattr(slack_routes, "is_kitchen_channel", AsyncMock(return_value=kitchen))
    monkeypatch.setattr(slack_routes, "allow_solo_thread_followup", AsyncMock(return_value=True))
    background_tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_message_payload(text, "Ev-other-mention"))),
        cast(BackgroundTasks, background_tasks),
    )

    assert response["status"] == "ignored"
    assert background_tasks.tasks == []


async def test_kitchen_name_without_opt_in_does_not_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def channel_context(_channel_id: str, *, use_cache: bool = True) -> SlackChannelContext:
        return SlackChannelContext(
            name="team-kitchen", is_ext_shared=False, is_pending_ext_shared=False
        )

    monkeypatch.setattr(webhook_common, "resolve_slack_channel_context", channel_context)
    payload = _message_payload("just talking", "Ev-kitchen-no-opt-in")
    del payload["event"]["thread_ts"]
    background_tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(payload)), cast(BackgroundTasks, background_tasks)
    )

    assert response["status"] == "ignored"
    assert background_tasks.tasks == []


@pytest.mark.parametrize("message_kind", ["root", "reply", "bot", "edit", "blocks"])
async def test_pr_links_are_recorded_without_starting_runs(
    registry_db: None,
    monkeypatch: pytest.MonkeyPatch,
    message_kind: Literal["root", "reply", "bot", "edit", "blocks"],
) -> None:
    monkeypatch.setattr(slack_routes, "watch_post", AsyncMock())
    payload = _message_payload(
        "<http://www.github.com/Other/Repo/pull/12/files|PR> "
        "https://github.com/other/repo/pull/12#discussion "
        "https://github.com/other/repo/pull/13 "
        "https://github.com.evil/other/repo/pull/99",
        "Ev-pr-link",
    )
    payload["team_id"] = "T1"
    event = payload["event"]
    if message_kind == "root":
        del event["thread_ts"]
    elif message_kind == "bot":
        event.update({"user": "BOT", "bot_id": "B1", "subtype": "bot_message"})
    elif message_kind == "edit":
        payload = _message_update_payload(bot_message=True)
        payload["team_id"] = "T1"
        payload["event"]["message"]["text"] = event["text"]
    elif message_kind == "blocks":
        event["blocks"] = [{"type": "section", "text": {"type": "mrkdwn", "text": event["text"]}}]
        event["text"] = ""
    background_tasks = _FakeBackgroundTasks()

    for _ in range(2):
        response = await slack_routes.slack_webhook(
            cast(Request, _FakeRequest(payload)), cast(BackgroundTasks, background_tasks)
        )
        assert response["status"] == "ignored"

    for func, args in background_tasks.tasks:
        await func(*args)
    cast(AsyncMock, webhook_common.resolve_slack_thread_id).assert_not_awaited()
    async with postgres.session() as session:
        links = (await session.scalars(select(SlackPullRequestLink))).all()
    assert {link.pr_url for link in links} == {
        "https://github.com/other/repo/pull/12",
        "https://github.com/other/repo/pull/13",
    }
    assert all(
        link.team_id == "T1"
        and link.channel_id == "C1"
        and link.thread_ts
        == ("1786573369.551099" if message_kind == "root" else "1786573300.000000")
        and link.message_ts == "1786573369.551099"
        for link in links
    )
    payload["team_id"] = "T2"
    await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(payload)), cast(BackgroundTasks, background_tasks)
    )
    async with postgres.session() as session:
        links = (await session.scalars(select(SlackPullRequestLink))).all()
    assert len(links) == 4
    assert {link.team_id for link in links} == {"T1", "T2"}


async def _run_message_update_task(background_tasks: _FakeBackgroundTasks) -> None:
    func, args = background_tasks.tasks[0]
    await func(*args)


@pytest.mark.parametrize(
    ("field", "value"),
    [("triggering_user_id", "UOTHER"), ("agent_thread_id", "other-thread")],
)
async def test_message_update_background_task_rejects_mismatched_delivery_mapping(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    lookup_run = cast(AsyncMock, webhook_common.lookup_slack_run_mapping)
    delivered = dict(lookup_run.return_value)
    delivered[field] = value
    lookup_run.return_value = delivered
    process = AsyncMock()
    monkeypatch.setattr(slack_service, "process_slack_mention", process)
    background_tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_message_update_payload())),
        cast(BackgroundTasks, background_tasks),
    )
    await _run_message_update_task(background_tasks)

    assert response == {"status": "accepted", "message": "Slack update queued"}
    process.assert_not_awaited()


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("503"), webhook_common.SlackThreadMappingError("conflict")],
)
async def test_message_update_lookup_failure_is_logged_without_reply(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    monkeypatch.setattr(webhook_common, "lookup_slack_thread_id", AsyncMock(side_effect=failure))
    report = AsyncMock()
    process = AsyncMock()
    monkeypatch.setattr(slack_failures, "report_slack_failure", report)
    monkeypatch.setattr(slack_service, "process_slack_mention", process)
    background_tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_message_update_payload())),
        cast(BackgroundTasks, background_tasks),
    )
    await _run_message_update_task(background_tasks)

    assert response == {"status": "accepted", "message": "Slack update queued"}
    report.assert_not_awaited()
    process.assert_not_awaited()


async def test_message_update_claim_failure_is_logged_without_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        webhook_common, "claim_slack_event", AsyncMock(side_effect=RuntimeError("503"))
    )
    report = AsyncMock()
    monkeypatch.setattr(slack_failures, "report_slack_failure", report)
    background_tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_message_update_payload())),
        cast(BackgroundTasks, background_tasks),
    )

    assert response == {"status": "ignored", "reason": "Slack update could not be claimed"}
    assert background_tasks.tasks == []
    report.assert_not_awaited()


async def test_confirmed_message_update_failure_replies_to_owning_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = RuntimeError("boom")
    monkeypatch.setattr(
        webhook_common,
        "resolve_slack_channel_context",
        AsyncMock(
            side_effect=[
                SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=False),
                failure,
            ]
        ),
    )
    report = AsyncMock()
    monkeypatch.setattr(slack_failures, "report_slack_failure", report)
    background_tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_message_update_payload())),
        cast(BackgroundTasks, background_tasks),
    )
    await _run_message_update_task(background_tasks)

    assert response == {"status": "accepted", "message": "Slack update queued"}
    report.assert_awaited_once()
    await_args = report.await_args
    assert await_args is not None
    target, reported_failure = await_args.args
    assert target.channel_id == "C1"
    assert target.thread_ts == "1786573300.000000"
    assert target.agent_thread_id == "t1"
    assert reported_failure is failure


async def test_message_update_retries_until_delivery_mapping_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lookup_run = AsyncMock(
        side_effect=[
            None,
            {
                "run_id": "run-1",
                "thread_ts": "1786573300.000000",
                "triggering_user_id": "U1",
                "agent_thread_id": "t1",
            },
        ]
    )
    sleep = AsyncMock()
    process = AsyncMock()
    monkeypatch.setattr(webhook_common, "lookup_slack_run_mapping", lookup_run)
    monkeypatch.setattr(slack_routes, "_MESSAGE_UPDATE_RETRY_DELAYS", (0.1,))
    monkeypatch.setattr(slack_routes.asyncio, "sleep", sleep)
    monkeypatch.setattr(slack_service, "process_slack_mention", process)
    background_tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_message_update_payload())),
        cast(BackgroundTasks, background_tasks),
    )
    assert response["status"] == "accepted"
    sleep.assert_not_awaited()

    await _run_message_update_task(background_tasks)

    sleep.assert_awaited_once_with(0.1)
    process.assert_awaited_once()


async def test_message_update_rejects_changed_sender_identity() -> None:
    payload = _message_update_payload()
    payload["event"]["previous_message"]["user"] = "UOTHER"
    background_tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(payload)),
        cast(BackgroundTasks, background_tasks),
    )

    assert response == {"status": "ignored", "reason": "Updated message identity changed"}
    assert background_tasks.tasks == []
