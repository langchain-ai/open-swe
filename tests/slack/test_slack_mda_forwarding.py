"""``/webhooks/slack`` hands MDA's conversations to MDA's Slack channel, and only those."""

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock

import httpx
import httpx2
import pytest
from fastapi import BackgroundTasks, FastAPI
from pydantic import JsonValue
from standardwebhooks import Webhook
from starlette.datastructures import URL
from starlette.requests import Request

from openswe import dispatch, event_claims
from openswe.agent_api.tokens import verify_runtime_token
from openswe.mda_channels import api as channel_api
from openswe.slack import channel as slack_channel
from openswe.slack import events as slack_events
from openswe.slack import failures as slack_failures
from openswe.slack import mda_forwarding
from openswe.slack import routes as slack_routes
from openswe.slack import webhook as slack_service
from openswe.slack.allowed_bots import AllowedSlackBot
from openswe.slack.dm import CONCIERGE_TS
from openswe.slack.payloads import SlackChannelContext
from openswe.slack.request import SlackRequest
from openswe.webhooks import common as webhook_common
from tests.mda_helpers import mda_starts, stub_mda_runtime

_EVENTS_URL = mda_forwarding.TRIGGER_EVENTS_URL
_MDA_THREAD = str(uuid.uuid4())
_DM_THREAD = str(uuid.uuid4())
_SERVICE_SECRET = "5e" * 32  # noqa: S105 - test value
# The real lookups, which the fixture below stands in for in the webhook's tests.
_OWNED_THREAD = mda_forwarding.owned_thread
_STARTABLE_THREAD = mda_forwarding.startable_thread
_STARTABLE_CONCIERGE = mda_forwarding.startable_concierge
_JOIN_CONCIERGE = mda_forwarding.join_concierge
_ACCOUNT_LINK_NEEDED = slack_service.account_link_needed


class _FakeBackgroundTasks:
    def __init__(self) -> None:
        self.tasks: list[tuple[Callable[..., object], tuple[object, ...]]] = []
        self.kwargs: list[dict[str, object]] = []

    def add_task(self, func: Callable[..., object], *args: object, **kwargs: object) -> None:
        self.tasks.append((func, args))
        self.kwargs.append(kwargs)


class _FakeRequest:
    def __init__(self, payload: dict[str, JsonValue], headers: dict[str, str]) -> None:
        self.headers = headers
        self.url = URL("http://test/webhooks/slack")
        self._body = json.dumps(payload).encode()

    async def body(self) -> bytes:
        return self._body


def _mention(**event: JsonValue) -> dict[str, JsonValue]:
    """An app mention, with ``event``'s fields in place of its event's own."""
    return {
        "type": "event_callback",
        "event_id": "Ev-mda",
        "api_app_id": "A1",
        "team_id": "T1",
        "authorizations": [{"user_id": "BOT"}],
        "event": {
            "type": "app_mention",
            "channel": "C1",
            "ts": "1786573369.551099",
            "user": "U1",
            "text": "<@BOT> help",
            **event,
        },
    }


_SLACK_HEADERS = {
    "X-Slack-Signature": "v0=abc",
    "X-Slack-Request-Timestamp": "1786573369",
    "Content-Type": "application/json",
    "X-Slack-Retry-Num": "1",
    "X-Slack-Retry-Reason": "timeout",
    "User-Agent": "Slackbot 1.0",
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

    monkeypatch.setattr(slack_events.event_claims, "claim", AsyncMock(return_value=True))
    monkeypatch.setattr(webhook_common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(
        webhook_common, "resolve_slack_thread_id", AsyncMock(return_value="backend-thread")
    )
    monkeypatch.setattr(webhook_common, "resolve_slack_channel_context", channel_context)
    monkeypatch.setattr(webhook_common, "get_slack_repo_config", AsyncMock(return_value=None))
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USER_ID", "BOT")
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USERNAME", "openswe")
    monkeypatch.setattr(mda_forwarding, "remember", AsyncMock())
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=None))
    monkeypatch.setattr(mda_forwarding, "startable_thread", AsyncMock(return_value=None))
    monkeypatch.setattr(mda_forwarding, "startable_concierge", AsyncMock(return_value=None))
    monkeypatch.setattr(mda_forwarding, "claim_conversation", AsyncMock(return_value=_MDA_THREAD))
    monkeypatch.setattr(mda_forwarding, "claim_concierge", AsyncMock(return_value=_DM_THREAD))
    monkeypatch.setattr(mda_forwarding, "join_concierge", AsyncMock())
    monkeypatch.setattr(slack_service, "account_link_needed", AsyncMock(return_value=None))
    monkeypatch.setattr(slack_service, "slack_login", AsyncMock(return_value="ada"))


async def test_a_conversation_for_mda_goes_to_trigger_server_exactly_as_slack_sent_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    startable = AsyncMock(return_value=_MDA_THREAD)
    monkeypatch.setattr(mda_forwarding, "startable_thread", startable)
    request = _FakeRequest(_mention(), dict(_SLACK_HEADERS))
    tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, request), cast(BackgroundTasks, tasks)
    )

    assert response["status"] == "accepted"
    assert startable.await_args.kwargs["root_ts"] == "1786573369.551099"
    assert startable.await_args.kwargs["can_start"] is True
    cast(AsyncMock, slack_service.account_link_needed).assert_awaited_once_with("U1")
    cast(AsyncMock, mda_forwarding.claim_conversation).assert_awaited_once_with(
        "A1", "T1", "C1", "1786573369.551099", owner_login="ada"
    )
    [(task, (url, body, headers))] = tasks.tasks
    # The forward knows which message it carries, to tell its thread if it's lost.
    assert tasks.kwargs == [
        {
            "thread_id": _MDA_THREAD,
            "message_ts": "1786573369.551099",
            "event_id": "Ev-mda",
            "on_refused": None,
        }
    ]
    assert task is mda_forwarding.forward_to_trigger_server
    assert url == _EVENTS_URL
    assert body == await request.body()
    # Only what Trigger Server verifies Slack's signature with: no retry headers.
    assert headers == {
        "X-Slack-Signature": "v0=abc",
        "X-Slack-Request-Timestamp": "1786573369",
        "Content-Type": "application/json",
    }
    remembered = cast(AsyncMock, mda_forwarding.remember).await_args.args
    assert remembered[0].thread_id == _MDA_THREAD
    assert remembered[2] == "1786573369.551099"


async def test_a_person_who_cant_run_is_asked_to_connect_before_anything_is_claimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mda_forwarding, "startable_thread", AsyncMock(return_value=_MDA_THREAD))
    needed = slack_service.AccountLinkNeeded("U1", "ada@example.com", "unlinked")
    monkeypatch.setattr(slack_service, "account_link_needed", AsyncMock(return_value=needed))
    tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_mention(), dict(_SLACK_HEADERS))), cast(BackgroundTasks, tasks)
    )

    assert response == {"status": "accepted", "message": "Slack account link requested"}
    assert tasks.tasks == [(needed.prompt, ("C1", "1786573369.551099"))]
    cast(AsyncMock, mda_forwarding.claim_conversation).assert_not_awaited()
    cast(AsyncMock, mda_forwarding.remember).assert_not_awaited()


@pytest.mark.parametrize("failure", ["repo lookup fails", "duplicate delivery"])
async def test_a_message_that_never_reaches_mda_leaves_its_conversation_unclaimed(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    monkeypatch.setattr(mda_forwarding, "startable_thread", AsyncMock(return_value=_MDA_THREAD))
    if failure == "repo lookup fails":
        monkeypatch.setattr(
            webhook_common, "get_slack_repo_config", AsyncMock(side_effect=RuntimeError("down"))
        )
        monkeypatch.setattr(slack_failures, "report_slack_failure", AsyncMock(return_value="err-1"))
    else:
        monkeypatch.setattr(slack_events.event_claims, "claim", AsyncMock(return_value=False))
    tasks = _FakeBackgroundTasks()

    await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_mention(), dict(_SLACK_HEADERS))), cast(BackgroundTasks, tasks)
    )

    # Nothing was handed over, so the conversation stays free for the backend or a retry.
    cast(AsyncMock, mda_forwarding.claim_conversation).assert_not_awaited()
    assert tasks.tasks == []


async def test_the_connect_prompt_is_posted_once_per_slack_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mda_forwarding, "startable_thread", AsyncMock(return_value=_MDA_THREAD))
    needed = slack_service.AccountLinkNeeded("U1", None, "revoked")
    monkeypatch.setattr(slack_service, "account_link_needed", AsyncMock(return_value=needed))
    monkeypatch.setattr(slack_events.event_claims, "claim", AsyncMock(return_value=False))
    tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_mention(), dict(_SLACK_HEADERS))), cast(BackgroundTasks, tasks)
    )

    assert response == {"status": "ignored", "reason": "Duplicate Slack event delivery"}
    assert tasks.tasks == []


def _allowed_bot_mention() -> dict[str, JsonValue]:
    """A mention of Open SWE by another Slack app's bot."""
    return _mention(user="UBOT", bot_id="B1", app_id="A2", text="<@BOT> run the release")


async def test_an_allowed_bots_mention_starts_an_mda_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = AllowedSlackBot(
        team_id="T1",
        bot_id="B1",
        user_id="UBOT",
        app_id="A2",
        name="release-bot",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    monkeypatch.setattr(slack_routes, "resolve_allowed_slack_bot", AsyncMock(return_value=bot))
    startable = AsyncMock(return_value=_MDA_THREAD)
    monkeypatch.setattr(mda_forwarding, "startable_thread", startable)
    tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_allowed_bot_mention(), dict(_SLACK_HEADERS))),
        cast(BackgroundTasks, tasks),
    )

    assert response["status"] == "accepted"
    assert startable.await_args.kwargs["can_start"] is True
    # A bot links no account, and no person owns the conversation it started.
    cast(AsyncMock, slack_service.account_link_needed).assert_not_awaited()
    cast(AsyncMock, mda_forwarding.claim_conversation).assert_awaited_once_with(
        "A1", "T1", "C1", "1786573369.551099", owner_login=None
    )
    [(task, _args)] = tasks.tasks
    assert task is mda_forwarding.forward_to_trigger_server
    # The run MDA asks for is built for the bot, as the backend builds it.
    request = cast(AsyncMock, mda_forwarding.remember).await_args.args[0]
    assert (request.triggering_bot_id, request.triggering_bot_app_id) == ("B1", "A2")


async def test_a_bot_off_the_allow_list_starts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_routes, "resolve_allowed_slack_bot", AsyncMock(return_value=None))
    tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_allowed_bot_mention(), dict(_SLACK_HEADERS))),
        cast(BackgroundTasks, tasks),
    )

    assert response == {"status": "ignored", "reason": "Event from a bot"}
    assert tasks.tasks == []
    cast(AsyncMock, mda_forwarding.claim_conversation).assert_not_awaited()


async def test_every_other_conversation_stays_on_the_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_mention(), dict(_SLACK_HEADERS))), cast(BackgroundTasks, tasks)
    )

    assert response["status"] == "accepted"
    [(task, (request, _repo))] = tasks.tasks
    assert task is slack_service.process_slack_mention
    assert request.thread_id == "backend-thread"


@pytest.mark.parametrize("single_session", ["code_channel", "concierge_dm"])
async def test_a_reply_in_a_thread_mda_held_stays_with_a_single_session(
    monkeypatch: pytest.MonkeyPatch, single_session: str
) -> None:
    # A code channel or concierge DM turned on after MDA took one of its threads.
    root = "1786573300.000001"
    owned = AsyncMock(side_effect=lambda _channel, ts: _MDA_THREAD if ts == root else None)
    monkeypatch.setattr(mda_forwarding, "owned_thread", owned)
    monkeypatch.setattr(
        webhook_common, "is_code_channel", AsyncMock(return_value=single_session == "code_channel")
    )
    monkeypatch.setattr(
        slack_routes.User,
        "concierge_mode_for_slack",
        AsyncMock(return_value=single_session == "concierge_dm"),
    )
    event: dict[str, JsonValue] = {
        "type": "message",
        "text": "and also",
        "thread_ts": "1786573300.000001",
    }
    if single_session == "concierge_dm":
        event |= {"channel": "D1", "channel_type": "im"}
    reply = _mention(**event)
    tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(reply, dict(_SLACK_HEADERS))), cast(BackgroundTasks, tasks)
    )

    assert response["status"] == "accepted"
    [(task, (request, _repo))] = tasks.tasks
    assert task is slack_service.process_slack_mention
    assert request.thread_id == "backend-thread"
    # The concierge DM asks only whether MDA holds the DM's one conversation.
    assert all(call.args[1] != root for call in owned.await_args_list)


async def test_a_message_in_mdas_conversation_trigger_server_would_drop_runs_nowhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=_MDA_THREAD))
    reply = _mention(type="message", subtype="thread_broadcast", thread_ts="1786573300.000001")
    tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(reply, dict(_SLACK_HEADERS))), cast(BackgroundTasks, tasks)
    )

    assert response == {
        "status": "ignored",
        "reason": "MDA's Slack channel doesn't take this kind of message",
    }
    assert tasks.tasks == []


async def test_an_edit_in_mdas_conversation_is_ignored_and_says_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned = AsyncMock(return_value=_MDA_THREAD)
    monkeypatch.setattr(mda_forwarding, "owned_thread", owned)
    claim = AsyncMock(return_value=True)
    monkeypatch.setattr(webhook_common, "claim_slack_event", claim)
    message = {"type": "message", "user": "U1", "ts": "1786573369.551099"}
    edit = _mention(
        type="message",
        subtype="message_changed",
        ts="1786573400.000000",
        event_ts="1786573400.000000",
        message={**message, "text": "<@BOT> help, fixed"},
        previous_message={**message, "text": "<@BOT> help"},
    )
    tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(edit, dict(_SLACK_HEADERS))), cast(BackgroundTasks, tasks)
    )

    assert response == {
        "status": "ignored",
        "reason": "MDA's Slack channel doesn't take message edits",
    }
    owned.assert_awaited_once_with("C1", "1786573369.551099")
    claim.assert_not_awaited()
    assert tasks.tasks == []


@pytest.mark.parametrize(
    ("exists", "metadata", "started"),
    [
        (False, {}, False),
        (True, {"visibility": "private", webhook_common.PROVISIONAL_VISIBILITY_KEY: True}, False),
        (True, {"visibility": "private", webhook_common.PROVISIONAL_VISIBILITY_KEY: None}, True),
        (True, {}, True),
    ],
    ids=["new", "claimed-stub", "first-run-written", "legacy"],
)
def test_a_claimed_conversations_stub_has_not_started(
    exists: bool, metadata: dict[str, JsonValue], started: bool
) -> None:
    """So the message that opens it is still the first mention."""
    assert webhook_common.ThreadRecord(exists=exists, metadata=metadata).started is started


_PLAIN: dict[str, JsonValue] = {
    "subtype": "",
    "code_channel": False,
    "breakout": False,
}


@pytest.mark.parametrize(
    ("message", "starts"),
    [
        ({}, True),
        ({"subtype": "file_share"}, True),
        ({"subtype": "thread_broadcast"}, False),
        ({"code_channel": True}, False),
        ({"breakout": True}, False),
    ],
)
def test_mda_starts_only_conversations_its_slack_channel_can_run(
    message: dict[str, JsonValue], starts: bool
) -> None:
    assert mda_forwarding.mda_can_start(**(_PLAIN | message)) is starts


async def _post_endpoint(
    app: FastAPI,
    body: dict[str, JsonValue],
    signed: bool,
    path: str = "/slack/channel/events",
    msg_id: str = "msg_delivery-1",
) -> httpx.Response:
    content = json.dumps(body).encode()
    headers = {"content-type": "application/json"}
    if signed:
        timestamp = datetime.now(tz=UTC)
        headers |= {
            "webhook-id": msg_id,
            "webhook-timestamp": str(int(timestamp.timestamp())),
            "webhook-signature": Webhook(_SERVICE_SECRET).sign(msg_id, timestamp, content.decode()),
        }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(path, content=content, headers=headers)


async def test_the_endpoint_answers_only_mda_and_only_for_messages_it_was_handed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SERVICE_SECRET)
    monkeypatch.setattr(mda_forwarding, "recall", AsyncMock(return_value=None))
    app = FastAPI()
    app.include_router(slack_channel.router)
    call = {
        "event": {"key": "mention", "deliveryId": "d-1", "messageTs": "1.2", "payload": {}},
        "thread": {"id": _MDA_THREAD, "channel": {"name": "slack"}},
    }

    assert (await _post_endpoint(app, call, signed=False)).status_code == 401
    answered = await _post_endpoint(app, call, signed=True)
    assert answered.status_code == 200
    assert answered.json() is None


def _trigger_server(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx2.Request], httpx2.Response]
) -> list[httpx2.Request]:
    monkeypatch.setattr(mda_forwarding.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(mda_forwarding.time, "time", lambda: 1786573369.0)
    monkeypatch.setattr(mda_forwarding, "report_lost", AsyncMock(return_value=True))
    monkeypatch.setattr(mda_forwarding, "take_back", AsyncMock(return_value=True))
    monkeypatch.setattr(mda_forwarding, "channel_started", AsyncMock(return_value=True))
    seen: list[httpx2.Request] = []

    def recording(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(mda_forwarding, "pooled_transport", lambda: httpx2.MockTransport(recording))
    return seen


async def _forward(message_ts: str = "1.1") -> None:
    await mda_forwarding.forward_to_trigger_server(
        _EVENTS_URL,
        b"{}",
        {"X-Slack-Request-Timestamp": "1786573369"},
        thread_id=_MDA_THREAD,
        message_ts=message_ts,
        event_id="Ev-1",
    )


def _outcome(caplog: pytest.LogCaptureFixture) -> dict[str, object]:
    [record] = [r for r in caplog.records if hasattr(r, "forward_outcome")]
    keys = ("slack_event_id", "agent_thread_id", "forward_outcome", "status", "attempts")
    return {key: getattr(record, key) for key in keys}


async def test_a_delivered_forward_logs_its_outcome_and_reports_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    answers = iter([httpx2.Response(503), httpx2.Response(200)])
    _trigger_server(monkeypatch, lambda _request: next(answers))

    with caplog.at_level("INFO"):
        await _forward()

    assert _outcome(caplog) == {
        "slack_event_id": "Ev-1",
        "agent_thread_id": _MDA_THREAD,
        "forward_outcome": "delivered",
        "status": 200,
        "attempts": 2,
    }
    cast(AsyncMock, mda_forwarding.report_lost).assert_not_awaited()


async def test_a_refused_forward_tells_the_slack_thread(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    seen = _trigger_server(monkeypatch, lambda _request: httpx2.Response(403))

    await _forward()

    assert len(seen) == 1
    assert _outcome(caplog)["forward_outcome"] == "refused"
    cast(AsyncMock, mda_forwarding.report_lost).assert_awaited_once()
    assert cast(AsyncMock, mda_forwarding.report_lost).await_args.args[:2] == (
        _MDA_THREAD,
        "1.1",
    )


@pytest.mark.parametrize("status", [401, 503])
async def test_a_conversation_the_channel_never_started_is_answered_here_when_refused(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    # 401: no Slack app registered for MDA in this environment; 503 that never clears.
    # Its first message or a later one: either way nothing ran on MDA yet.
    _trigger_server(monkeypatch, lambda _request: httpx2.Response(status))
    monkeypatch.setattr(mda_forwarding, "channel_started", AsyncMock(return_value=False))

    await _forward()

    cast(AsyncMock, mda_forwarding.take_back).assert_awaited_once_with(_MDA_THREAD, "1.1")
    cast(AsyncMock, mda_forwarding.report_lost).assert_not_awaited()


async def test_a_forward_that_gives_up_tells_the_slack_thread(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    seen = _trigger_server(monkeypatch, lambda _request: httpx2.Response(502))

    await _forward()

    assert len(seen) == 5
    assert _outcome(caplog) | {"agent_thread_id": None} == {
        "slack_event_id": "Ev-1",
        "agent_thread_id": None,
        "forward_outcome": "gave_up",
        "status": 502,
        "attempts": 5,
    }
    cast(AsyncMock, mda_forwarding.report_lost).assert_awaited_once()


async def test_a_lost_stop_reports_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _trigger_server(monkeypatch, lambda _request: httpx2.Response(400))

    await _forward(message_ts="")

    assert _outcome(caplog)["forward_outcome"] == "refused"
    cast(AsyncMock, mda_forwarding.report_lost).assert_not_awaited()


_CALL = {
    "event": {"key": "mention", "deliveryId": "d-1", "messageTs": "1.2", "payload": {}},
    "thread": {"id": _MDA_THREAD, "channel": {"name": "slack"}},
}


async def test_a_run_that_fails_to_come_together_is_reported_in_slack_not_a_500(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SERVICE_SECRET)
    request = SlackRequest(channel_id="C1", thread_ts="1.2", thread_id=_MDA_THREAD)
    monkeypatch.setattr(mda_forwarding, "recall", AsyncMock(return_value=(request, None)))
    failure = RuntimeError("metadata write failed")
    monkeypatch.setattr(slack_service, "prepare_slack_channel_run", AsyncMock(side_effect=failure))
    notify = AsyncMock()
    monkeypatch.setattr(slack_service, "notify_slack_processing_error", notify)
    app = FastAPI()
    app.include_router(slack_channel.router)

    with caplog.at_level("INFO"):
        answered = await _post_endpoint(app, _CALL, signed=True)

    assert answered.status_code == 200
    assert answered.json() is None
    notify.assert_awaited_once_with(request, None, failure)
    [record] = [r for r in caplog.records if hasattr(r, "no_run_reason")]
    assert record.no_run_reason == "building the run failed"
    assert record.agent_thread_id == _MDA_THREAD


async def test_the_endpoint_logs_why_it_answers_no_run(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SERVICE_SECRET)
    request = SlackRequest(channel_id="C1", thread_ts="1.2", thread_id=_MDA_THREAD)
    monkeypatch.setattr(mda_forwarding, "recall", AsyncMock(return_value=(request, None)))
    monkeypatch.setattr(slack_service, "prepare_slack_channel_run", AsyncMock(return_value=None))
    app = FastAPI()
    app.include_router(slack_channel.router)

    with caplog.at_level("INFO"):
        answered = await _post_endpoint(app, _CALL, signed=True)

    assert answered.json() is None
    [record] = [r for r in caplog.records if hasattr(r, "no_run_reason")]
    assert record.no_run_reason == "no run prepared"


async def test_mda_starts_the_run_a_handed_over_message_prepares(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SERVICE_SECRET)
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "test-signing-key-0123456789abcdef")
    request = SlackRequest(channel_id="C1", thread_ts="1.2", thread_id=_MDA_THREAD)
    monkeypatch.setattr(mda_forwarding, "recall", AsyncMock(return_value=(request, None)))
    prepared = slack_service.SlackChannelRun(
        input={"messages": [{"role": "user", "content": "help"}]},
        configurable={"source": "slack", "github_login": "ada"},
        trigger_ts="1.2",
    )
    monkeypatch.setattr(
        slack_service, "prepare_slack_channel_run", AsyncMock(return_value=prepared)
    )
    monkeypatch.setattr(slack_channel.index, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(slack_channel, "channel_run_starting", AsyncMock())
    monkeypatch.setattr(
        dispatch,
        "_run_user_metadata",
        AsyncMock(
            return_value={
                "ls_repo_provider": "github",
                "user_id": "user-1",
                "ls_attribution_identifier": "ada",
            }
        ),
    )
    stub_mda_runtime(monkeypatch)
    app = FastAPI()
    app.include_router(slack_channel.router)

    answered = await _post_endpoint(app, _CALL, signed=True)

    assert answered.status_code == 200
    answer = answered.json()
    # The attribution the run's hooks read rides in its token.
    token = verify_runtime_token(answer["context"]["__run_token"])
    assert token.thread_id == _MDA_THREAD
    assert token.metadata["ls_attribution_identifier"] == "ada"
    # MDA starts the run: the answer sets none of the metadata keys MDA keeps.
    metadata = mda_starts(answer)["metadata"]
    assert metadata == answer["metadata"]
    assert metadata["channel"] == "slack"
    assert metadata["slack_trigger_ts"] == "1.2"
    assert not {"user_id", "ls_repo_provider", "ls_attribution_identifier"} & metadata.keys()


_PLAN = {"thread_id": _MDA_THREAD, "message_ts": "1.2"}


async def _plan(app: FastAPI) -> httpx.Response:
    return await _post_endpoint(app, _PLAN, signed=True, path="/mda/channel-api/plan_slack_turn")


@pytest.mark.parametrize("interrupts", [True, False])
async def test_the_handler_gets_the_run_and_whether_it_redirects_the_work_in_progress(
    monkeypatch: pytest.MonkeyPatch, interrupts: bool
) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SERVICE_SECRET)
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "test-signing-key-0123456789abcdef")
    request = SlackRequest(channel_id="C1", thread_ts="1.2", thread_id=_MDA_THREAD)
    recall = AsyncMock(return_value=(request, None))
    monkeypatch.setattr(mda_forwarding, "recall", recall)
    prepared = slack_service.SlackChannelRun(
        input={"messages": [{"role": "user", "content": "and the tests too"}]},
        configurable={"source": "slack", "github_login": "ada"},
        trigger_ts="1.2",
        interrupts=interrupts,
    )
    monkeypatch.setattr(
        slack_service, "prepare_slack_channel_run", AsyncMock(return_value=prepared)
    )
    monkeypatch.setattr(slack_channel.index, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(slack_channel, "channel_run_starting", AsyncMock())
    monkeypatch.setattr(dispatch, "_run_user_metadata", AsyncMock(return_value={}))
    stub_mda_runtime(monkeypatch)
    app = FastAPI()
    app.include_router(channel_api.router)

    answered = await _plan(app)

    assert answered.status_code == 200
    start = answered.json()
    assert start["interrupts"] is interrupts
    assert start["run"]["input"] == prepared.input
    assert verify_runtime_token(start["run"]["context"]["__run_token"]).thread_id == _MDA_THREAD
    assert start["run"]["metadata"]["slack_trigger_ts"] == "1.2"
    # A call sent again under the same webhook ID builds the run again.
    recall.assert_awaited_once_with(_MDA_THREAD, "1.2", "msg_delivery-1")


async def test_the_handler_gets_no_run_for_a_message_the_backend_didnt_hand_over(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SERVICE_SECRET)
    monkeypatch.setattr(mda_forwarding, "recall", AsyncMock(return_value=None))
    app = FastAPI()
    app.include_router(channel_api.router)

    with caplog.at_level("INFO"):
        answered = await _plan(app)

    assert answered.status_code == 200
    assert answered.json() == {"run": None, "interrupts": True, "thread_id": None}
    [record] = [r for r in caplog.records if hasattr(r, "no_run_reason")]
    assert record.no_run_reason == "not handed over"


@pytest.mark.parametrize(
    ("token", "record", "needed"),
    [
        ("gho_valid", False, None),
        (None, True, "revoked"),
        (None, False, "unlinked"),
    ],
)
async def test_the_account_gate_runs_without_a_thread(
    monkeypatch: pytest.MonkeyPatch, token: str | None, record: bool, needed: str | None
) -> None:
    monkeypatch.setattr(
        webhook_common,
        "get_slack_user_info",
        AsyncMock(return_value={"profile": {"email": "ada@example.com"}}),
    )
    monkeypatch.setattr(slack_service, "slack_login", AsyncMock(return_value="ada"))
    monkeypatch.setattr(webhook_common, "get_valid_access_token", AsyncMock(return_value=token))
    monkeypatch.setattr(webhook_common, "has_access_token_record", AsyncMock(return_value=record))

    gate = await _ACCOUNT_LINK_NEEDED("U1")

    if needed is None:
        assert gate is None
    else:
        assert gate == slack_service.AccountLinkNeeded("U1", "ada@example.com", needed)


async def test_a_kitchen_channels_untagged_message_starts_an_mda_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_routes, "is_kitchen_channel", AsyncMock(return_value=True))
    startable = AsyncMock(return_value=_MDA_THREAD)
    monkeypatch.setattr(mda_forwarding, "startable_thread", startable)
    message = _mention(type="message", text="the nightly build is red")
    tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(message, dict(_SLACK_HEADERS))), cast(BackgroundTasks, tasks)
    )

    assert response == {"status": "accepted", "message": "Slack message handed to MDA"}
    assert startable.await_args.kwargs["can_start"] is True
    # Each Slack thread stays its own conversation.
    cast(AsyncMock, mda_forwarding.claim_conversation).assert_awaited_once_with(
        "A1", "T1", "C1", "1786573369.551099", owner_login="ada"
    )
    [(task, _)] = tasks.tasks
    assert task is mda_forwarding.forward_to_trigger_server
    # The backend routes the run to the channel's workspace when it builds it.
    request = cast(AsyncMock, mda_forwarding.remember).await_args.args[0]
    assert request.kitchen_channel is True
    assert request.treat_all_messages_as_mentions is True


async def test_a_later_untagged_message_in_a_kitchen_thread_follows_it_to_mda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_routes, "is_kitchen_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=_MDA_THREAD))
    reply = _mention(type="message", text="still red", thread_ts="1786573300.000001")
    tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(reply, dict(_SLACK_HEADERS))), cast(BackgroundTasks, tasks)
    )

    assert response == {"status": "accepted", "message": "Slack message handed to MDA"}
    assert tasks.kwargs[0]["thread_id"] == _MDA_THREAD


def _dm(**event: JsonValue) -> dict[str, JsonValue]:
    return _mention(type="message", channel="D1", channel_type="im", text="fix it", **event)


async def test_a_new_concierge_dm_becomes_one_mda_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_routes.User, "concierge_mode_for_slack", AsyncMock(return_value=True))
    startable = AsyncMock(return_value=_DM_THREAD)
    monkeypatch.setattr(mda_forwarding, "startable_concierge", startable)
    tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_dm(), dict(_SLACK_HEADERS))), cast(BackgroundTasks, tasks)
    )

    assert response == {"status": "accepted", "message": "Slack message handed to MDA"}
    assert startable.await_args.kwargs == {
        "app_id": "A1",
        "team_id": "T1",
        "channel_id": "D1",
        "can_start": True,
    }
    cast(AsyncMock, mda_forwarding.claim_concierge).assert_awaited_once_with(
        "A1", "T1", "D1", owner_login="ada"
    )
    cast(AsyncMock, mda_forwarding.claim_conversation).assert_not_awaited()
    # Slack's Stop in this Slack thread, and the channel asking about it, find the DM's thread.
    cast(AsyncMock, mda_forwarding.join_concierge).assert_awaited_once_with(
        "D1", "1786573369.551099", _DM_THREAD
    )
    request = cast(AsyncMock, mda_forwarding.remember).await_args.args[0]
    assert (request.thread_id, request.thread_ts, request.concierge_mode) == (
        _DM_THREAD,
        CONCIERGE_TS,
        True,
    )
    assert tasks.kwargs[0]["thread_id"] == _DM_THREAD


async def test_a_concierge_dm_mda_holds_takes_every_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_routes.User, "concierge_mode_for_slack", AsyncMock(return_value=True))
    owned = AsyncMock(side_effect=lambda _channel, ts: _DM_THREAD if ts == CONCIERGE_TS else None)
    monkeypatch.setattr(mda_forwarding, "owned_thread", owned)
    tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_dm(thread_ts="1786573300.000001"), dict(_SLACK_HEADERS))),
        cast(BackgroundTasks, tasks),
    )

    assert response == {"status": "accepted", "message": "Slack message handed to MDA"}
    cast(AsyncMock, mda_forwarding.startable_concierge).assert_not_awaited()
    cast(AsyncMock, mda_forwarding.join_concierge).assert_awaited_once_with(
        "D1", "1786573300.000001", _DM_THREAD
    )
    assert tasks.kwargs[0]["thread_id"] == _DM_THREAD


async def test_a_concierge_dm_the_backend_holds_stays_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_routes.User, "concierge_mode_for_slack", AsyncMock(return_value=True))
    tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_dm(), dict(_SLACK_HEADERS))), cast(BackgroundTasks, tasks)
    )

    assert response["status"] == "accepted"
    [(task, (request, _repo))] = tasks.tasks
    assert task is slack_service.process_slack_mention
    assert request.thread_ts == CONCIERGE_TS
    cast(AsyncMock, mda_forwarding.claim_concierge).assert_not_awaited()


async def test_an_edit_in_a_concierge_dm_mda_holds_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_routes.User, "concierge_mode_for_slack", AsyncMock(return_value=True))
    owned = AsyncMock(side_effect=lambda _channel, ts: _DM_THREAD if ts == CONCIERGE_TS else None)
    monkeypatch.setattr(mda_forwarding, "owned_thread", owned)
    message = {"type": "message", "user": "U1", "ts": "1786573369.551099"}
    edit = _mention(
        type="message",
        subtype="message_changed",
        channel="D1",
        channel_type="im",
        ts="1786573400.000000",
        event_ts="1786573400.000000",
        message={**message, "text": "fix it, please"},
        previous_message={**message, "text": "fix it"},
    )
    tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(edit, dict(_SLACK_HEADERS))), cast(BackgroundTasks, tasks)
    )

    assert response == {
        "status": "ignored",
        "reason": "MDA's Slack channel doesn't take message edits",
    }
    assert tasks.tasks == []


async def test_owned_conversation_finds_a_concierge_dms_thread_for_an_unthreaded_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned = AsyncMock(side_effect=lambda _channel, ts: _DM_THREAD if ts == CONCIERGE_TS else None)
    monkeypatch.setattr(mda_forwarding, "owned_thread", owned)

    assert await mda_forwarding.owned_conversation("D1", "171.5") == _DM_THREAD
    # A channel has no DM-wide conversation.
    assert await mda_forwarding.owned_conversation("C1", "171.5") is None


class _InteractivityRequest:
    def __init__(self, payload: dict[str, JsonValue]) -> None:
        from urllib.parse import urlencode  # noqa: PLC0415

        self.headers = dict(_SLACK_HEADERS) | {"Content-Type": "application/x-www-form-urlencoded"}
        self.url = URL("http://test/webhooks/slack/interactivity")
        self._body = urlencode({"payload": json.dumps(payload)}).encode()

    async def body(self) -> bytes:
        return self._body


def _click(value: dict[str, JsonValue], *, channel: str = "C1") -> dict[str, JsonValue]:
    return {
        "type": "block_actions",
        "trigger_id": "trigger-1",
        "api_app_id": "A1",
        "team": {"id": "T1"},
        "user": {"id": "U1"},
        "channel": {"id": channel},
        "container": {"type": "message", "channel_id": channel, "message_ts": "171.5"},
        "message": {"ts": "171.5", "thread_ts": "171.1", "text": "Which one?", "blocks": []},
        "response_url": "https://hooks.slack.com/actions/T1/1/abc",
        "actions": [
            {
                "type": "button",
                "action_id": "open_swe_option_select_1",
                "action_ts": "171.6",
                "value": json.dumps(value),
            }
        ],
    }


async def _interact(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, JsonValue]
) -> tuple[object, _FakeBackgroundTasks, _InteractivityRequest]:
    monkeypatch.setattr(slack_routes.EventLog, "record", AsyncMock())
    monkeypatch.setattr(
        slack_routes.User, "concierge_mode_for_slack", AsyncMock(return_value=False)
    )
    request = _InteractivityRequest(payload)
    tasks = _FakeBackgroundTasks()
    response = await slack_routes.slack_interactivity(
        cast(Request, request), cast(BackgroundTasks, tasks)
    )
    return response, tasks, request


@pytest.mark.parametrize(
    "value",
    [
        {"type": "open_swe_option", "response": "Option B", "user_id": "U1"},
        {"type": "workflow_push_approval", "action": "approve", "fingerprint": "f1"},
        {"type": "slack_retry", "response": "a.signed.token"},
        {"type": "expedited_review", "action": "approve", "fingerprint": "r1"},
        {"type": "human_review", "action": "review", "fingerprint": "r1"},
        {"type": "act_as", "action": "approve", "fingerprint": "f1", "thread_id": "t1"},
    ],
    ids=["option", "workflow push", "token retry", "expedited review", "human review", "act as"],
)
async def test_every_click_goes_to_trigger_server_as_slack_sent_it(
    monkeypatch: pytest.MonkeyPatch, value: dict[str, JsonValue]
) -> None:
    response, tasks, request = await _interact(monkeypatch, _click(value))

    assert response == {"status": "accepted", "message": "Slack click handed to MDA"}
    [(task, (url, body, headers))] = tasks.tasks
    assert task is mda_forwarding.forward_to_trigger_server
    assert url == mda_forwarding.TRIGGER_INTERACTIVITY_URL
    assert body == await request.body()
    assert headers == {
        "X-Slack-Signature": "v0=abc",
        "X-Slack-Request-Timestamp": "1786573369",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    [kwargs] = tasks.kwargs
    # A click remembers no message; one Trigger Server refuses is acted on here.
    assert (kwargs["thread_id"], kwargs["message_ts"], kwargs["event_id"]) == ("", "", "trigger-1")
    assert callable(kwargs["on_refused"])


async def test_a_modal_trigger_server_opened_closes_when_handed_to_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submission = {
        "type": "view_submission",
        "team": {"id": "T1"},
        "user": {"id": "U1"},
        "trigger_id": "trigger-1",
        "view": {"id": "V1", "callback_id": "mda_modal::expedited_review_channel:r1"},
    }

    response, tasks, _ = await _interact(monkeypatch, submission)

    # Slack shows an error on a submitted modal for any answer but an empty one.
    assert response == {}
    [(task, _)] = tasks.tasks
    assert task is mda_forwarding.forward_to_trigger_server


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "message_action", "callback_id": "open_swe_explain", "user": {"id": "U1"}},
        {"type": "block_suggestion", "container": {"type": "code_channel_view"}, "value": "a"},
        {
            "type": "block_actions",
            "container": {"type": "code_channel_view", "view_id": "V1"},
            "user": {"id": "U1"},
            "actions": [],
        },
        {
            "type": "view_submission",
            "user": {"id": "U1"},
            "view": {"callback_id": "human_review_decline", "private_metadata": "{}"},
        },
    ],
    ids=["explain this", "options load", "code channel view", "backend-opened modal"],
)
async def test_explain_this_code_channel_views_and_the_backends_modals_stay_here(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, JsonValue]
) -> None:
    act = AsyncMock(return_value={})
    monkeypatch.setattr(slack_routes, "act_on_interaction", act)

    _, tasks, _ = await _interact(monkeypatch, payload)

    assert tasks.tasks == []
    act.assert_awaited_once()


async def test_a_click_trigger_server_refuses_is_acted_on_here(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acted: list[str] = []

    async def act(interaction: object, _raw: str, tasks: BackgroundTasks, **_: object) -> dict:
        tasks.add_task(acted.append, cast(slack_routes.SlackInteraction, interaction).user.id)
        return {}

    monkeypatch.setattr(slack_routes, "act_on_interaction", act)
    _, tasks, _ = await _interact(
        monkeypatch, _click({"type": "expedited_review", "action": "approve", "fingerprint": "r1"})
    )

    await cast(Callable[[], Awaitable[None]], tasks.kwargs[0]["on_refused"])()

    assert acted == ["U1"]


async def test_the_handler_finds_a_concierge_dms_message_by_its_slack_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SERVICE_SECRET)
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "test-signing-key-0123456789abcdef")
    request = SlackRequest(
        channel_id="D1", thread_ts=CONCIERGE_TS, thread_id=_DM_THREAD, concierge_mode=True
    )
    # Remembered on the DM's thread, not on the Slack thread's own.
    recall = AsyncMock(
        side_effect=lambda thread, *_: (request, None) if thread == _DM_THREAD else None
    )
    monkeypatch.setattr(mda_forwarding, "recall", recall)
    monkeypatch.setattr(mda_forwarding, "owned_conversation", AsyncMock(return_value=_DM_THREAD))
    _prepared(monkeypatch)
    app = FastAPI()
    app.include_router(channel_api.router)

    answered = await _post_endpoint(
        app,
        {**_PLAN, "channel_id": "D1", "thread_ts": "1.2"},
        signed=True,
        path="/mda/channel-api/plan_slack_turn",
    )

    start = answered.json()
    assert start["thread_id"] == _DM_THREAD
    assert verify_runtime_token(start["run"]["context"]["__run_token"]).thread_id == _DM_THREAD
    cast(AsyncMock, mda_forwarding.owned_conversation).assert_awaited_once_with("D1", "1.2")


def _prepared(monkeypatch: pytest.MonkeyPatch, interrupts: bool = False) -> AsyncMock:
    prepared = slack_service.SlackChannelRun(
        input={"messages": [{"role": "user", "content": "Option B"}]},
        configurable={"source": "slack", "github_login": "ada"},
        trigger_ts="171.6",
        interrupts=interrupts,
    )
    prepare = AsyncMock(return_value=prepared)
    monkeypatch.setattr(slack_service, "prepare_slack_channel_run", prepare)
    monkeypatch.setattr(slack_channel.index, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(slack_channel, "channel_run_starting", AsyncMock())
    monkeypatch.setattr(dispatch, "_run_user_metadata", AsyncMock(return_value={}))
    stub_mda_runtime(monkeypatch)
    return prepare


class _Claims:
    """``event_claims``, in memory."""

    def __init__(self) -> None:
        self.held: set[tuple[str, str]] = set()

    async def claim(self, scope: str, key: str, *, ttl: object) -> bool:
        if (scope, key) in self.held:
            return False
        self.held.add((scope, key))
        return True

    async def is_held(self, scope: str, key: str) -> bool:
        return (scope, key) in self.held


def _click_app(monkeypatch: pytest.MonkeyPatch, *, owner: str | None = _MDA_THREAD) -> FastAPI:
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SERVICE_SECRET)
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "test-signing-key-0123456789abcdef")
    claims = _Claims()
    monkeypatch.setattr(event_claims, "claim", claims.claim)
    monkeypatch.setattr(event_claims, "held", claims.is_held)
    monkeypatch.setattr(mda_forwarding, "owned_conversation", AsyncMock(return_value=owner))
    app = FastAPI()
    app.include_router(channel_api.router)
    return app


def _plan_click(click: dict[str, JsonValue], **fields: JsonValue) -> dict[str, JsonValue]:
    return {
        "thread_id": _MDA_THREAD,
        "channel_id": "C1",
        "thread_ts": "171.1",
        "message_ts": "171.5",
        "action_ts": "171.6",
        "user_id": "U1",
        "team_id": "T1",
        "click": click,
        **fields,
    }


async def _post_click(
    app: FastAPI, body: dict[str, JsonValue], msg_id: str = "msg_delivery-1"
) -> dict[str, JsonValue]:
    answered = await _post_endpoint(
        app, body, signed=True, path="/mda/channel-api/plan_slack_click", msg_id=msg_id
    )
    assert answered.status_code == 200, answered.text
    return answered.json()


async def test_an_option_click_is_the_persons_message_and_is_acted_on_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _click_app(monkeypatch)
    prepare = _prepared(monkeypatch)
    body = _plan_click({"kind": "option", "text": "Option B"})

    first = await _post_click(app, body)
    resent = await _post_click(app, body)
    other = await _post_click(app, body, msg_id="msg_delivery-2")

    assert first["acted"] is True
    assert first["interrupts"] is False
    assert first["thread_id"] is None
    assert verify_runtime_token(first["run"]["context"]["__run_token"]).thread_id == _MDA_THREAD
    # MDA sending the same call again gets the run again; any other click on the message, none.
    assert resent["run"] is not None
    assert other == {
        "run": None,
        "interrupts": True,
        "thread_id": None,
        "acted": False,
        "backend": False,
        "workflow_push": None,
    }
    request = prepare.await_args_list[0].args[0]
    assert (request.text, request.user_id, request.thread_id, request.event_ts) == (
        "Option B",
        "U1",
        _MDA_THREAD,
        "171.6",
    )


async def test_a_click_in_a_concierge_dm_runs_on_the_dms_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _click_app(monkeypatch, owner=_DM_THREAD)
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=_DM_THREAD))
    prepare = _prepared(monkeypatch)

    start = await _post_click(
        app,
        _plan_click(
            {"kind": "option", "text": "Option B"},
            channel_id="D1",
            thread_ts="171.5",
        ),
    )

    assert start["thread_id"] == _DM_THREAD
    request = prepare.await_args.args[0]
    # The button hung off an unthreaded message, so the answer goes to the DM itself.
    assert (request.thread_ts, request.reply_thread_ts, request.concierge_mode) == (
        CONCIERGE_TS,
        "",
        True,
    )


async def test_a_click_in_the_backends_conversation_is_the_backends_to_act_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _click_app(monkeypatch, owner=None)
    prepare = _prepared(monkeypatch)

    start = await _post_click(app, _plan_click({"kind": "option", "text": "Option B"}))

    assert (start["acted"], start["backend"]) == (False, True)
    prepare.assert_not_awaited()


async def _act(
    app: FastAPI, payload: dict[str, JsonValue], msg_id: str = "msg_delivery-1", **fields: str
) -> dict[str, JsonValue]:
    body = {"channel_id": "C1", "thread_ts": "171.1", "user_id": "U1", "payload": payload}
    answered = await _post_endpoint(
        app,
        cast(dict[str, JsonValue], body | fields),
        signed=True,
        path="/mda/channel-api/act_on_slack_click",
        msg_id=msg_id,
    )
    assert answered.status_code == 200, answered.text
    return answered.json()


async def test_the_backend_acts_on_a_click_once_as_the_clicker_trigger_server_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _click_app(monkeypatch)
    act = AsyncMock(return_value={})
    monkeypatch.setattr(slack_routes, "act_on_interaction", act)
    # Slack's payload names someone else: only Trigger Server's verified clicker counts.
    payload = _click({"type": "expedited_review", "action": "approve", "fingerprint": "r1"})
    payload["user"] = {"id": "U9", "name": "someone"}

    first = await _act(app, payload)
    again = await _act(app, payload)

    assert (first["acted"], again["acted"]) == (True, False)
    [call] = act.await_args_list
    interaction = call.args[0]
    assert interaction.user.id == "U1"
    assert call.kwargs["origin"] == ("C1", "171.1")


async def test_a_review_card_click_reaches_the_cards_own_handling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.expedited_review import slack as expedited  # noqa: PLC0415

    app = _click_app(monkeypatch)
    monkeypatch.setattr(
        slack_routes.User, "concierge_mode_for_slack", AsyncMock(return_value=False)
    )
    vote = AsyncMock()
    monkeypatch.setattr(expedited, "process_vote", vote)

    await _act(app, _click({"type": "expedited_review", "action": "approve", "fingerprint": "r1"}))

    vote.assert_awaited_once()
    assert vote.await_args.args == ("r1",)
    kwargs = vote.await_args.kwargs
    assert (kwargs["decision"], kwargs["person"], kwargs["thread_ts"]) == (
        "approve",
        {"id": "slack:U1"},
        "171.1",
    )
    # The answer goes back through the click's response_url.
    assert kwargs["response_url"] == "https://hooks.slack.com/actions/T1/1/abc"


def _submission(callback_id: str, values: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {
        "type": "view_submission",
        "team": {"id": "T1"},
        "user": {"id": "U1"},
        "view": {"id": "V1", "callback_id": callback_id, "state": {"values": values}},
    }


async def test_a_pick_modal_trigger_server_opened_acts_on_its_pick_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.human_review import slack as human  # noqa: PLC0415

    app = _click_app(monkeypatch)
    process = AsyncMock()
    monkeypatch.setattr(human, "_process_on_message", process)
    choice = {
        "choice": {"choice": {"type": "static_select", "selected_option": {"value": "1 hour"}}}
    }

    await _act(
        app,
        _submission("mda_modal::human_review_snooze:r1", choice),
        channel_id="D1",
        thread_ts="171.5",
    )

    process.assert_awaited_once_with(
        "r1",
        "snooze",
        channel_id="D1",
        message_ts="171.5",
        slack_user_id="U1",
        choice="1 hour",
    )


async def test_a_pick_modal_with_a_choice_it_never_offered_does_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.human_review import slack as human  # noqa: PLC0415

    app = _click_app(monkeypatch)
    process = AsyncMock()
    monkeypatch.setattr(human, "_process_on_message", process)
    forged = {
        "choice": {"choice": {"type": "static_select", "selected_option": {"value": "1 year"}}}
    }

    await _act(app, _submission("mda_modal::human_review_snooze:r1", forged))

    process.assert_not_awaited()


async def test_the_channel_picker_trigger_server_opened_sends_the_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.expedited_review import slack as expedited  # noqa: PLC0415

    app = _click_app(monkeypatch)
    vote = AsyncMock()
    monkeypatch.setattr(expedited, "process_vote", vote)
    picked = {
        "channel": {"channel": {"type": "conversations_select", "selected_conversation": "C9"}}
    }

    await _act(app, _submission("mda_modal::expedited_review_channel:r1", picked))

    vote.assert_awaited_once()
    assert vote.await_args.kwargs["decision"] == "send"
    assert vote.await_args.kwargs["target_channel"] == "C9"
    assert (vote.await_args.kwargs["channel_id"], vote.await_args.kwargs["thread_ts"]) == (
        "C1",
        "171.1",
    )


@pytest.mark.parametrize(("approved", "status"), [(True, "approved"), (False, "rejected")])
async def test_a_workflow_push_is_decided_under_the_backends_lock(
    monkeypatch: pytest.MonkeyPatch, approved: bool, status: str
) -> None:
    app = _click_app(monkeypatch)
    prepare = _prepared(monkeypatch, interrupts=False)
    decide = AsyncMock(return_value=({"status": status}, True))
    monkeypatch.setattr(webhook_common, "decide_workflow_push_approval", decide)

    start = await _post_click(
        app, _plan_click({"kind": "workflow_push", "fingerprint": "f1", "approved": approved})
    )

    assert start["workflow_push"] == status
    decide.assert_awaited_once_with(_MDA_THREAD, "f1", approved=approved, actor="U1")
    assert (start["run"] is not None) is approved
    if approved:
        assert "Retry the blocked git push" in prepare.await_args.args[0].text


async def test_a_workflow_push_decided_before_starts_nothing_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _click_app(monkeypatch)
    prepare = _prepared(monkeypatch)
    monkeypatch.setattr(
        webhook_common,
        "decide_workflow_push_approval",
        AsyncMock(return_value=({"status": "approved"}, False)),
    )

    start = await _post_click(
        app, _plan_click({"kind": "workflow_push", "fingerprint": "f1", "approved": True})
    )

    assert (start["acted"], start["workflow_push"], start["run"]) == (False, "approved", None)
    prepare.assert_not_awaited()


async def test_a_retry_sends_the_requesters_message_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.slack import retry  # noqa: PLC0415

    app = _click_app(monkeypatch)
    prepare = _prepared(monkeypatch, interrupts=True)
    monkeypatch.setattr(
        retry,
        "fetch_slack_thread_message_by_ts",
        AsyncMock(return_value={"type": "message", "user": "U1", "ts": "171.2", "text": "fix CI"}),
    )
    monkeypatch.setattr(webhook_common, "is_code_channel", AsyncMock(return_value=False))
    monkeypatch.setattr(retry, "is_kitchen_channel", AsyncMock(return_value=False))

    start = await _post_click(
        app, _plan_click({"kind": "retry", "thread_ts": "171.1", "message_ts": "171.2"})
    )

    assert start["interrupts"] is True
    request = prepare.await_args.args[0]
    assert (request.text, request.user_id, request.thread_id, request.retry_message_ts) == (
        "fix CI",
        "U1",
        _MDA_THREAD,
        "171.2",
    )


@pytest.mark.parametrize(
    ("can_start", "held_by", "startable"),
    [(True, None, True), (True, "backend-thread", False), (False, None, False)],
    ids=["new", "held", "can't start"],
)
async def test_mda_starts_a_concierge_dm_only_while_no_thread_holds_it(
    monkeypatch: pytest.MonkeyPatch, can_start: bool, held_by: str | None, startable: bool
) -> None:
    lookup = AsyncMock(return_value=held_by)
    monkeypatch.setattr(mda_forwarding, "lookup_slack_thread_id", lookup)

    thread_id = await _STARTABLE_CONCIERGE(
        app_id="A1", team_id="T1", channel_id="D1", can_start=can_start
    )

    expected = mda_forwarding.mda_thread_id("A1", "T1", "D1", CONCIERGE_TS)
    assert thread_id == (expected if startable else None)
    if can_start:
        lookup.assert_awaited_once_with("D1", CONCIERGE_TS)


async def test_a_slack_thread_another_thread_holds_keeps_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind = AsyncMock(side_effect=webhook_common.SlackThreadMappingError("taken"))
    monkeypatch.setattr(mda_forwarding, "bind_slack_thread_id", bind)

    await _JOIN_CONCIERGE("D1", "171.1", _DM_THREAD)
    await _JOIN_CONCIERGE("D1", CONCIERGE_TS, _DM_THREAD)

    bind.assert_awaited_once_with("D1", "171.1", _DM_THREAD)


async def test_explain_this_through_trigger_server_is_answered_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _click_app(monkeypatch)
    ask = AsyncMock()
    monkeypatch.setattr(slack_routes, "process_slack_ask", ask)
    shortcut: dict[str, JsonValue] = {
        "type": "message_action",
        "callback_id": "open_swe_explain",
        "trigger_id": "trigger-1",
        "team": {"id": "T1"},
        "user": {"id": "U9"},
        "channel": {"id": "C1"},
        "message": {"ts": "171.2", "thread_ts": "171.1", "text": "What does this do?"},
        "response_url": "https://hooks.slack.com/app/T1/1/abc",
    }

    await _act(app, shortcut)

    ask.assert_awaited_once()
    [request] = ask.await_args.args
    # As the person Trigger Server verified, answered through the shortcut's response_url.
    assert (request.user_id, request.selected_message, request.response_url) == (
        "U1",
        "What does this do?",
        "https://hooks.slack.com/app/T1/1/abc",
    )
