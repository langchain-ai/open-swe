"""``/webhooks/slack`` leaves MDA's conversations to MDA's Slack channel, and only those;
the channel API answers what MDA's Slack channel asks about them."""

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import JsonValue
from runtime_api.slack import (
    SLACK_MOVE_MARKER,
    ActOnSlackMessage,
    SlackTurnPrompt,
)
from standardwebhooks import Webhook
from starlette.datastructures import URL
from starlette.requests import Request

from openswe import dispatch, event_claims
from openswe.agent_api.tokens import verify_runtime_token
from openswe.mda_channels import api as channel_api
from openswe.slack import ask as slack_ask
from openswe.slack import channel as slack_channel
from openswe.slack import events as slack_events
from openswe.slack import failures as slack_failures
from openswe.slack import forks, mda_forwarding
from openswe.slack import routes as slack_routes
from openswe.slack import webhook as slack_service
from openswe.slack.allowed_bots import AllowedSlackBot
from openswe.slack.channels import SlackChannel
from openswe.slack.dm import CONCIERGE_TS
from openswe.slack.payloads import SlackChannelContext
from openswe.slack.request import SlackRequest
from openswe.threads import index
from openswe.threads.index import IndexedThread
from openswe.webhooks import common as webhook_common
from tests.mda_helpers import mda_starts, stub_mda_runtime
from tests.support.slack_api import SlackAPI

pytestmark = pytest.mark.usefixtures("registry_db")


_MDA_THREAD = str(uuid.uuid4())
_DM_THREAD = str(uuid.uuid4())
_SERVICE_SECRET = "5e" * 32  # noqa: S105 - test value
# The real lookups, which the fixture below stands in for in the webhook's tests.
_OWNED_THREAD = mda_forwarding.owned_thread
_JOIN_CONCIERGE = mda_forwarding.join_concierge
_CHANNEL_CONTEXT = webhook_common.resolve_slack_channel_context


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
    monkeypatch.setattr(slack_service.WORKSPACES, "is_ask_channel", AsyncMock(return_value=False))
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
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=None))
    monkeypatch.setattr(mda_forwarding, "claim_conversation", AsyncMock(return_value=_MDA_THREAD))
    monkeypatch.setattr(mda_forwarding, "claim_concierge", AsyncMock(return_value=_DM_THREAD))
    monkeypatch.setattr(mda_forwarding, "claim_side", AsyncMock())
    monkeypatch.setattr(mda_forwarding, "join_concierge", AsyncMock())
    monkeypatch.setattr(slack_service, "account_link_needed", AsyncMock(return_value=None))
    monkeypatch.setattr(slack_service, "slack_login", AsyncMock(return_value="ada"))


async def _webhook(
    payload: dict[str, JsonValue],
) -> tuple[object, _FakeBackgroundTasks, _FakeRequest]:
    request = _FakeRequest(payload, dict(_SLACK_HEADERS))
    tasks = _FakeBackgroundTasks()
    response = await slack_routes.slack_webhook(
        cast(Request, request), cast(BackgroundTasks, tasks)
    )
    return response, tasks, request


async def test_a_new_conversation_slack_delivers_here_is_answered_here(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An environment whose Slack app points at the backend: no Trigger Server takes it.
    process = AsyncMock()
    monkeypatch.setattr(slack_service, "process_slack_mention", process)

    response, tasks, _request = await _webhook(_mention())

    assert response == {"status": "accepted", "message": "Slack mention queued"}
    [(task, (request, repo))] = tasks.tasks
    assert task is process
    assert cast(SlackRequest, request).thread_id == "backend-thread"
    assert repo is None


async def test_a_message_in_mdas_conversation_slack_delivers_here_gets_an_error_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=_MDA_THREAD))
    notify = AsyncMock()
    monkeypatch.setattr(slack_service, "notify_slack_processing_error", notify)
    process = AsyncMock()
    monkeypatch.setattr(slack_service, "process_slack_mention", process)

    response, tasks, _request = await _webhook(_mention(thread_ts="1786573300.000001"))

    assert cast(dict[str, str], response)["status"] == "ignored"
    [(report, args)] = tasks.tasks
    await cast(Callable[..., Awaitable[None]], report)(*args)
    [request, repo, error] = notify.await_args.args
    assert cast(SlackRequest, request).thread_id == _MDA_THREAD
    assert repo is None
    assert isinstance(error, mda_forwarding.MdaHandoffLost)
    process.assert_not_awaited()


@pytest.mark.parametrize("failure", ["repo lookup fails", "duplicate delivery"])
async def test_a_message_that_fails_or_repeats_starts_nothing(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    if failure == "repo lookup fails":
        monkeypatch.setattr(
            webhook_common, "get_slack_repo_config", AsyncMock(side_effect=RuntimeError("down"))
        )
        monkeypatch.setattr(slack_failures, "report_slack_failure", AsyncMock(return_value="err-1"))
    else:
        monkeypatch.setattr(slack_events.event_claims, "claim", AsyncMock(return_value=False))

    _response, tasks, _request = await _webhook(_mention())

    assert tasks.tasks == []


def _allowed_bot_mention() -> dict[str, JsonValue]:
    """A mention of Open SWE by another Slack app's bot."""
    return _mention(user="UBOT", bot_id="B1", app_id="A2", text="<@BOT> run the release")


_BOT = AllowedSlackBot(
    team_id="T1",
    bot_id="B1",
    user_id="UBOT",
    app_id="A2",
    name="release-bot",
    created_at=datetime(2026, 1, 1, tzinfo=UTC),
)


async def test_a_bot_off_the_allow_list_starts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_routes, "resolve_allowed_slack_bot", AsyncMock(return_value=None))

    response, tasks, _request = await _webhook(_allowed_bot_mention())

    assert response == {"status": "ignored", "reason": "Event from a bot"}
    assert tasks.tasks == []


async def test_every_other_conversation_stays_on_the_backend() -> None:
    response, tasks, _request = await _webhook(_mention())

    assert response["status"] == "accepted"
    [(task, (request, _repo))] = tasks.tasks
    assert task is slack_service.process_slack_mention
    assert request.thread_id == "backend-thread"


async def _hand_back(
    event: dict[str, JsonValue], webhook_id: str = "msg_1"
) -> tuple[object, _FakeBackgroundTasks]:
    """A message MDA's Slack channel hands back, as ``act_on_slack_message`` gets it."""
    tasks = _FakeBackgroundTasks()
    call = channel_api.ChannelCall(
        request=cast(Request, _FakeRequest({}, {"webhook-id": webhook_id})),
        background=cast(BackgroundTasks, tasks),
    )
    message = ActOnSlackMessage(team_id="T1", app_id="A1", delivery_id="d-1", event=event)
    return await slack_channel.act_on_slack_message(call, message), tasks


async def test_a_message_mda_hands_back_is_answered_here() -> None:
    # Slack's Events URL points at Trigger Server: a conversation the backend holds
    # is heard only this way, and runs on its own thread as before.
    event = cast(dict[str, JsonValue], _mention()["event"])

    acted, tasks = await _hand_back(event)

    assert acted.acted
    [(task, (request, _repo))] = tasks.tasks
    assert task is slack_service.process_slack_mention
    assert request.thread_id == "backend-thread"


async def test_a_message_mda_hands_back_never_goes_to_mda_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Even if MDA's channel holds the thread by now, MDA already decided not to take this
    # message: the backend starts nothing and posts no "won't run" notice.
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=_MDA_THREAD))
    event = cast(dict[str, JsonValue], _mention()["event"])

    _acted, tasks = await _hand_back(event)

    assert tasks.tasks == []


async def test_a_message_mda_hands_back_again_is_acted_on_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_channel.event_claims, "claim", AsyncMock(return_value=False))
    event = cast(dict[str, JsonValue], _mention()["event"])

    acted, tasks = await _hand_back(event)

    assert not acted.acted
    assert tasks.tasks == []


async def test_a_message_mda_hands_back_that_fails_is_run_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A 503 while Slack can't confirm the channel acted on nothing: the call's claim goes,
    # so MDA's retry under the same webhook ID runs it.
    released = AsyncMock()
    monkeypatch.setattr(slack_channel.event_claims, "release", released)
    monkeypatch.setattr(
        slack_routes.SlackEventsDelivery,
        "handle",
        AsyncMock(side_effect=HTTPException(503, "Slack channel verification unavailable")),
    )
    event = cast(dict[str, JsonValue], _mention()["event"])

    with pytest.raises(HTTPException):
        await _hand_back(event)

    released.assert_awaited_once_with("slack_message_call", "act:msg_1")


async def test_a_reply_in_a_thread_mda_held_stays_with_a_single_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Concierge mode turned on after MDA took one of its threads.
    root = "1786573300.000001"
    owned = AsyncMock(side_effect=lambda _channel, ts: _MDA_THREAD if ts == root else None)
    monkeypatch.setattr(mda_forwarding, "owned_thread", owned)
    monkeypatch.setattr(
        slack_routes.User,
        "concierge_mode_for_slack",
        AsyncMock(return_value=True),
    )
    event: dict[str, JsonValue] = {
        "type": "message",
        "text": "and also",
        "thread_ts": "1786573300.000001",
    }
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


async def test_an_edit_in_mdas_conversation_starts_nothing_here(
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

    # The conversation is MDA's: an edit Slack delivers here is only logged.
    assert cast(dict[str, str], response)["status"] == "ignored"
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


async def _post_endpoint(
    app: FastAPI,
    body: dict[str, JsonValue],
    signed: bool,
    path: str = "/mda/channel-api/start_slack_turn",
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


def _api(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SERVICE_SECRET)
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "test-signing-key-0123456789abcdef")
    app = FastAPI()
    app.include_router(channel_api.router)
    return app


def _message(**fields: JsonValue) -> dict[str, JsonValue]:
    """A Slack message as MDA's Slack channel describes it to the backend."""
    return {
        "thread_id": _MDA_THREAD,
        "app_id": "A1",
        "team_id": "T1",
        "channel_id": "C1",
        "message_ts": "1.2",
        "thread_ts": "1.2",
        "user_id": "U1",
        "text": "<@BOT> help",
        "explicit_mention": True,
        **fields,
    }


async def test_the_channel_api_answers_only_mda(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _api(monkeypatch)

    for path in (
        "/mda/channel-api/slack_message_facts",
        "/mda/channel-api/start_slack_turn",
        "/mda/channel-api/act_on_slack_message",
    ):
        answered = await _post_endpoint(app, _message(), signed=False, path=path)
        assert answered.status_code == 401
    # The endpoint channels once named is gone: MDA builds its turns from the event.
    assert (
        await _post_endpoint(app, {}, signed=True, path="/slack/channel/events")
    ).status_code == 404


@pytest.fixture
def facts(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock | Mock]:
    """Open SWE's records as ``slack_message_facts`` reads them: a plain public channel."""
    lookups: dict[str, AsyncMock | Mock] = {
        "kitchen": AsyncMock(return_value=False),
        "concierge": AsyncMock(return_value=False),
        "bot": AsyncMock(return_value=None),
        "solo": AsyncMock(return_value=False),
        "track": Mock(),
        "owned": AsyncMock(return_value=None),
        "held": AsyncMock(return_value=None),
        "indexed": AsyncMock(return_value=None),
        "workspace": AsyncMock(return_value=None),
    }
    monkeypatch.setattr(slack_channel, "is_kitchen_channel", lookups["kitchen"])
    monkeypatch.setattr(slack_channel.User, "concierge_mode_for_slack", lookups["concierge"])
    monkeypatch.setattr(slack_channel, "resolve_allowed_slack_bot", lookups["bot"])
    monkeypatch.setattr(slack_channel, "allow_solo_thread_followup", lookups["solo"])
    monkeypatch.setattr(slack_channel, "track_explicit_mention", lookups["track"])
    monkeypatch.setattr(mda_forwarding, "owned_thread", lookups["owned"])
    monkeypatch.setattr(slack_channel, "lookup_slack_thread_id", lookups["held"])
    monkeypatch.setattr(slack_channel.index, "get", lookups["indexed"])
    monkeypatch.setattr(slack_channel.WORKSPACES, "get", lookups["workspace"])
    return lookups


async def _facts(app: FastAPI, **fields: JsonValue) -> httpx.Response:
    return await _post_endpoint(
        app, _message(**fields), signed=True, path="/mda/channel-api/slack_message_facts"
    )


async def test_facts_of_a_mention_in_a_plain_channel(
    monkeypatch: pytest.MonkeyPatch, facts: dict[str, AsyncMock | Mock]
) -> None:
    answered = await _facts(_api(monkeypatch))

    assert answered.status_code == 200, answered.text
    assert answered.json() == {
        "channel": "ok",
        "dm": False,
        "kitchen_channel": False,
        "concierge": False,
        "allowed_bot": False,
        "bot_name": "",
        "solo_followup": False,
        "conversation": "new",
        "workspace_known": False,
    }
    # A mention counts toward who talks in the thread, for later untagged replies.
    assert facts["track"].call_args.kwargs["message_ts"] == "1.2"
    facts["solo"].assert_not_awaited()


async def test_an_untagged_reply_the_agent_answers_is_a_solo_followup(
    monkeypatch: pytest.MonkeyPatch, facts: dict[str, AsyncMock | Mock]
) -> None:
    facts["solo"].return_value = True

    answered = await _facts(
        _api(monkeypatch), text="and the tests", explicit_mention=False, reply_thread_ts="1.0"
    )

    assert answered.json()["solo_followup"] is True
    facts["track"].assert_not_called()


async def test_a_kitchen_channels_message_is_one(
    monkeypatch: pytest.MonkeyPatch, facts: dict[str, AsyncMock | Mock]
) -> None:
    facts["kitchen"].return_value = True

    answered = await _facts(_api(monkeypatch), text="the nightly build is red")

    assert answered.json()["kitchen_channel"] is True


async def test_a_concierge_dms_conversation_is_the_dms_one_thread(
    monkeypatch: pytest.MonkeyPatch, facts: dict[str, AsyncMock | Mock]
) -> None:
    facts["concierge"].return_value = True
    facts["owned"].side_effect = lambda _channel, ts: _DM_THREAD if ts == CONCIERGE_TS else None

    answered = await _facts(_api(monkeypatch), channel_id="D1", channel_type="im")

    assert {key: answered.json()[key] for key in ("dm", "concierge", "conversation")} == {
        "dm": True,
        "concierge": True,
        "conversation": "mda",
    }
    # A DM has no kitchen and no solo follow-ups.
    facts["kitchen"].assert_not_awaited()
    facts["track"].assert_not_called()


async def test_an_allowed_bot_is_named_as_the_allow_list_names_it(
    monkeypatch: pytest.MonkeyPatch, facts: dict[str, AsyncMock | Mock]
) -> None:
    facts["bot"].return_value = _BOT

    answered = await _facts(_api(monkeypatch), user_id="UBOT", bot_id="B1", bot_app_id="A2")

    assert (answered.json()["allowed_bot"], answered.json()["bot_name"]) == (True, "release-bot")
    facts["bot"].assert_awaited_once_with("T1", "B1", user_id="UBOT", app_id="A2")


@pytest.mark.parametrize(
    ("owned", "held", "indexed", "conversation"),
    [
        (_MDA_THREAD, None, None, "mda"),
        (None, "backend-thread", None, "backend"),
        (None, None, None, "new"),
        (None, None, object(), "backend"),
    ],
    ids=["mda", "backend", "new", "moved away"],
)
async def test_facts_say_who_holds_the_conversation(
    monkeypatch: pytest.MonkeyPatch,
    facts: dict[str, AsyncMock | Mock],
    owned: str | None,
    held: str | None,
    indexed: object,
    conversation: str,
) -> None:
    facts["owned"].return_value = owned
    facts["held"].return_value = held
    facts["indexed"].return_value = indexed

    answered = await _facts(_api(monkeypatch))

    assert answered.json()["conversation"] == conversation
    if conversation == "new":
        # A conversation that moved away took its thread, which the index still has.
        facts["indexed"].assert_awaited_once_with(
            mda_forwarding.mda_thread_id("A1", "T1", "C1", "1.2")
        )


@pytest.mark.parametrize(("workspace", "known"), [(None, False), (object(), True)])
async def test_facts_say_whether_a_workspace_tag_names_a_workspace(
    monkeypatch: pytest.MonkeyPatch,
    facts: dict[str, AsyncMock | Mock],
    workspace: object,
    known: bool,
) -> None:
    facts["workspace"].return_value = workspace

    answered = await _facts(_api(monkeypatch), workspace_tag="infra")

    assert answered.json()["workspace_known"] is known
    facts["workspace"].assert_awaited_once_with("infra")


def _channel_is(monkeypatch: pytest.MonkeyPatch, channel: SlackChannelContext) -> None:
    async def channel_context(_channel_id: str, *, use_cache: bool = True) -> SlackChannelContext:
        assert use_cache is False
        return channel

    monkeypatch.setattr(webhook_common, "resolve_slack_channel_context", channel_context)


@pytest.mark.parametrize(
    ("channel", "refused"),
    [
        (SlackChannelContext(id="C1", is_ext_shared=True, is_pending_ext_shared=False), "external"),
        (
            SlackChannelContext(id="C1", is_ext_shared=False, is_pending_ext_shared=True),
            "unverified",
        ),
    ],
)
async def test_a_channel_open_swe_doesnt_operate_in_is_said_so(
    monkeypatch: pytest.MonkeyPatch,
    facts: dict[str, AsyncMock | Mock],
    channel: SlackChannelContext,
    refused: str,
) -> None:
    _channel_is(monkeypatch, channel)

    answered = await _facts(_api(monkeypatch))

    assert answered.json()["channel"] == refused
    facts["owned"].assert_not_awaited()


async def test_a_channel_slack_cant_confirm_yet_fails_the_call_so_it_comes_again(
    monkeypatch: pytest.MonkeyPatch, facts: dict[str, AsyncMock | Mock]
) -> None:
    _channel_is(monkeypatch, SlackChannelContext(id="C1", lookup_retry_after=7))

    answered = await _facts(_api(monkeypatch))

    assert answered.status_code == 503
    assert answered.headers["retry-after"] == "7"


def _prepared(
    monkeypatch: pytest.MonkeyPatch,
    interrupts: bool = False,
    prompt: SlackTurnPrompt | None = None,
    content: str = "Option B",
) -> AsyncMock:
    prepared = slack_service.SlackChannelRun(
        input={"messages": [{"role": "user", "content": content}]},
        configurable={"source": "slack", "github_login": "ada"},
        trigger_ts="171.6",
        interrupts=interrupts,
    )
    prepare = AsyncMock(return_value=(prepared, prompt))
    monkeypatch.setattr(slack_service, "prepare_slack_channel_run", prepare)
    monkeypatch.setattr(slack_channel.index, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(slack_channel, "channel_run_starting", AsyncMock())
    monkeypatch.setattr(
        dispatch, "_run_user_metadata", AsyncMock(return_value={"billing_bucket": "system"})
    )
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


def _start_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    claims = _Claims()
    monkeypatch.setattr(event_claims, "claim", claims.claim)
    monkeypatch.setattr(event_claims, "held", claims.is_held)
    monkeypatch.setattr(
        slack_service.LangSmithLogin,
        "slack_onboarding_url",
        lambda: "https://openswe.test/settings",
    )
    return _api(monkeypatch)


async def _start(app: FastAPI, msg_id: str = "msg_delivery-1", **fields: JsonValue) -> dict:
    answered = await _post_endpoint(app, _message(**fields), signed=True, msg_id=msg_id)
    assert answered.status_code == 200, answered.text
    return answered.json()


_NO_RUN = {
    "run": None,
    "interrupts": True,
    "thread_id": None,
    "branches_conversation": False,
    "declined": None,
    "prompt": None,
}
_PROMPT = SlackTurnPrompt(
    first_turn=True,
    sender_name="Ada",
    logins={"U1": "ada"},
    person_ids={"U1": "user:1"},
    channel={"id": "slack:C1", "platform": "slack"},
)


@pytest.mark.parametrize("interrupts", [True, False])
async def test_a_turn_gets_its_prompts_facts_and_a_signed_run_for_mdas_prompt(
    monkeypatch: pytest.MonkeyPatch, slack_api: SlackAPI, interrupts: bool
) -> None:
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=_MDA_THREAD))
    monkeypatch.setattr(webhook_common, "resolve_slack_channel_context", _CHANNEL_CONTEXT)
    await SlackChannel.save_all({"id": "C1", "name": "engineering"})
    prepare = _prepared(monkeypatch, interrupts=interrupts, prompt=_PROMPT)
    prepare.return_value = (
        slack_service.SlackChannelRun(
            input={"messages": []},
            configurable={"source": "slack"},
            trigger_ts="1.2",
            # A concierge DM's message, which the backend marks ⏳ until the run ends.
            concierge_message_ts="1.1",
            interrupts=interrupts,
        ),
        _PROMPT,
    )
    app = _start_app(monkeypatch)

    start = await _start(
        app,
        clean_text="@openswe help",
        context_user_ids=["U2", "U1"],
        participant_user_ids=["U1", "U2"],
        has_images=True,
        review_card=True,
    )

    assert start["interrupts"] is interrupts
    assert start["thread_id"] is None
    # MDA's channel builds the prompt: the run comes with no input, and with the facts.
    assert start["run"]["input"] == {"messages": []}
    assert start["prompt"] == _PROMPT.model_dump(mode="json")
    assert verify_runtime_token(start["run"]["context"]["__run_token"]).thread_id == _MDA_THREAD
    assert start["run"]["metadata"]["slack_trigger_ts"] == "1.2"
    # The run-complete webhook takes the ⏳ off the concierge message it names.
    assert start["run"]["metadata"]["slack_concierge_message_ts"] == "1.1"
    [(request, repo)] = [prepare.await_args.args]
    assert (request.thread_id, request.thread_ts, request.event_ts, request.text) == (
        _MDA_THREAD,
        "1.2",
        "1.2",
        "<@BOT> help",
    )
    assert repo is None
    assert prepare.await_args.kwargs == {
        "read": slack_service.SlackTurnRead(
            clean_text="@openswe help",
            context_user_ids=["U2", "U1"],
            participant_user_ids=["U1", "U2"],
            has_images=True,
            review_card=True,
        ),
    }
    cast(AsyncMock, slack_service.account_link_needed).assert_awaited_once_with("U1")
    cast(AsyncMock, mda_forwarding.claim_conversation).assert_not_awaited()
    assert request.channel_context.name == "engineering"
    assert slack_api.calls == []


@pytest.mark.parametrize("trigger_kind", ["user", "slack_bot"])
async def test_the_attribution_the_runs_hooks_read_rides_in_its_token(
    monkeypatch: pytest.MonkeyPatch,
    trigger_kind: str,
) -> None:
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=_MDA_THREAD))
    _prepared(monkeypatch, prompt=_PROMPT)
    monkeypatch.setattr(
        dispatch,
        "_run_user_metadata",
        AsyncMock(
            return_value={
                "ls_repo_provider": "github",
                "billing_bucket": "user",
                "user_id": "user-1",
                "ls_attribution_identifier": "ada",
            }
        ),
    )

    classification: dict[str, JsonValue] = {
        "trigger_kind": trigger_kind,
        "origin": "slack",
        "source": "slack",
        "owner_type": "system" if trigger_kind == "slack_bot" else "user",
        "thread_category": "interactive",
        "owner_login": "ada",
    }
    if trigger_kind == "slack_bot":
        classification["triggering_bot"] = "T1:B1"
    now = datetime.now(UTC)
    monkeypatch.setattr(
        index,
        "get",
        AsyncMock(
            return_value=IndexedThread(
                thread_id=_MDA_THREAD,
                runtime="mda",
                metadata=classification,
                status="idle",
                created_at=now,
                updated_at=now,
            )
        ),
    )

    start = await _start(_start_app(monkeypatch))

    token = verify_runtime_token(start["run"]["context"]["__run_token"])
    assert token.metadata["ls_attribution_identifier"] == "ada"
    # MDA starts the run: the answer sets none of the metadata keys MDA keeps.
    metadata = mda_starts({key: value for key, value in start["run"].items() if value})["metadata"]
    assert metadata["channel"] == "slack"
    assert classification.items() <= metadata.items()
    assert not {"user_id", "ls_repo_provider", "ls_attribution_identifier"} & metadata.keys()


@pytest.mark.parametrize(
    ("channel_id", "channel_type", "external"),
    [("C1", "channel", False), ("C1", "channel", True), ("D1", "im", False)],
    ids=["internal", "newly external", "dm"],
)
async def test_a_new_conversation_is_claimed_once_its_sender_passed_the_gate(
    monkeypatch: pytest.MonkeyPatch,
    slack_api: SlackAPI,
    channel_id: str,
    channel_type: str,
    external: bool,
) -> None:
    monkeypatch.setattr(webhook_common, "resolve_slack_channel_context", _CHANNEL_CONTEXT)
    await SlackChannel.save_all(
        {
            "id": channel_id,
            "is_im": channel_type == "im",
            "is_ext_shared": False,
            "is_pending_ext_shared": False,
        }
    )
    if channel_type != "im":
        slack_api.respond(
            {
                "ok": True,
                "channel": {
                    "id": channel_id,
                    "is_im": False,
                    "is_ext_shared": external,
                    "is_pending_ext_shared": False,
                },
            }
        )
    prepare = _prepared(monkeypatch, prompt=_PROMPT)

    start = await _start(_start_app(monkeypatch), channel_id=channel_id, channel_type=channel_type)

    assert start["run"] is not None
    cast(AsyncMock, slack_service.account_link_needed).assert_awaited_once_with("U1")
    context = SlackChannelContext(
        id=channel_id,
        is_im=channel_type == "im",
        is_ext_shared=external,
        is_pending_ext_shared=False,
    )
    cast(AsyncMock, mda_forwarding.claim_conversation).assert_awaited_once_with(
        "A1", "T1", channel_id, "1.2", owner_login="ada", channel_context=context
    )
    assert prepare.await_args.args[0].channel_context == context
    assert slack_api.calls == (
        [] if channel_type == "im" else [("conversations.info", {"channel": channel_id})]
    )


async def test_an_unlinked_ask_sender_can_start_without_a_personal_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepared(monkeypatch, prompt=_PROMPT)
    monkeypatch.setattr(slack_service.WORKSPACES, "is_ask_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(
        slack_service,
        "account_link_needed",
        AsyncMock(return_value=slack_service.AccountLinkNeeded("U1", None, "unlinked")),
    )
    monkeypatch.setattr(slack_service, "slack_login", AsyncMock(return_value=None))
    prompt = AsyncMock()
    monkeypatch.setattr(slack_service.AccountLinkNeeded, "prompt", prompt)

    start = await _start(_start_app(monkeypatch))

    assert start["run"] is not None
    cast(AsyncMock, mda_forwarding.claim_conversation).assert_awaited_once_with(
        "A1",
        "T1",
        "C1",
        "1.2",
        owner_login=None,
        channel_context=SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=False),
    )
    prompt.assert_not_awaited()


@pytest.mark.parametrize("needed", ["unlinked", "revoked"])
async def test_a_person_who_cant_run_is_prompted_privately_before_anything_is_claimed_once_per_call(
    monkeypatch: pytest.MonkeyPatch, needed: str
) -> None:
    gate = slack_service.AccountLinkNeeded(
        "U1", None, cast(webhook_common.AccountLinkReason, needed)
    )
    prompt = AsyncMock()
    monkeypatch.setattr(slack_service.AccountLinkNeeded, "prompt", prompt)
    monkeypatch.setattr(slack_service, "account_link_needed", AsyncMock(return_value=gate))
    prepare = _prepared(monkeypatch)
    app = _start_app(monkeypatch)

    first = await _start(app)
    again = await _start(app)
    other_call = await _start(app, msg_id="msg_delivery-2")

    assert first == _NO_RUN
    prompt.assert_awaited_once_with("C1", "1.2")
    # MDA sends a call again under its webhook ID: the decline was answered already.
    assert again == _NO_RUN
    # Another event of the same message, as Slack sends a mention twice, answers nothing.
    assert other_call == _NO_RUN
    cast(AsyncMock, mda_forwarding.claim_conversation).assert_not_awaited()
    prepare.assert_not_awaited()


async def test_a_message_slack_sends_twice_starts_one_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=_MDA_THREAD))
    prepare = _prepared(monkeypatch, prompt=_PROMPT)
    app = _start_app(monkeypatch)

    # Slack sends a mention as app_mention and as message; Trigger Server forwards both.
    mention = await _start(app)
    message = await _start(app, msg_id="msg_delivery-2")
    again = await _start(app)

    assert mention["run"] is not None
    assert message == _NO_RUN
    # MDA sends the first call again under its webhook ID: its run is built again.
    assert again["run"] is not None
    assert prepare.await_count == 2


async def test_an_edit_of_a_message_that_started_a_turn_is_not_claimed_as_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=_MDA_THREAD))
    _prepared(monkeypatch, prompt=_PROMPT)
    app = _start_app(monkeypatch)
    claim = AsyncMock(return_value=True)
    monkeypatch.setattr(event_claims, "claim", claim)

    await _start(app, kind="edit", msg_id="msg_delivery-2")

    assert all(call.args[0] != "slack_turn" for call in claim.await_args_list)


async def test_an_allowed_bots_turn_links_no_account_and_no_person_owns_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare = _prepared(monkeypatch, prompt=_PROMPT)

    await _start(_start_app(monkeypatch), user_id="UBOT", bot_id="B1", bot_app_id="A2")

    cast(AsyncMock, slack_service.account_link_needed).assert_not_awaited()
    cast(AsyncMock, mda_forwarding.claim_conversation).assert_awaited_once_with(
        "A1",
        "T1",
        "C1",
        "1.2",
        owner_login=None,
        channel_context=SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=False),
    )
    request = prepare.await_args.args[0]
    assert (request.triggering_bot_id, request.triggering_bot_app_id) == ("B1", "A2")


async def test_a_new_concierge_dm_becomes_one_mda_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare = _prepared(monkeypatch, prompt=_PROMPT)

    start = await _start(
        _start_app(monkeypatch),
        channel_id="D1",
        channel_type="im",
        thread_ts="1.0",
        reply_thread_ts="1.0",
        concierge=True,
    )

    cast(AsyncMock, mda_forwarding.claim_concierge).assert_awaited_once_with(
        "A1", "T1", "D1", owner_login="ada"
    )
    cast(AsyncMock, mda_forwarding.claim_conversation).assert_not_awaited()
    # Slack's Stop in this Slack thread, and its later messages, find the DM's thread.
    cast(AsyncMock, mda_forwarding.join_concierge).assert_awaited_once_with("D1", "1.0", _DM_THREAD)
    request = prepare.await_args.args[0]
    assert (request.thread_id, request.thread_ts, request.reply_thread_ts) == (
        _DM_THREAD,
        CONCIERGE_TS,
        "1.0",
    )
    assert request.concierge_mode is True
    assert start["thread_id"] == _DM_THREAD
    assert verify_runtime_token(start["run"]["context"]["__run_token"]).thread_id == _DM_THREAD


async def test_a_concierge_dm_mda_holds_takes_every_slack_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned = AsyncMock(side_effect=lambda _channel, ts: _DM_THREAD if ts == CONCIERGE_TS else None)
    monkeypatch.setattr(mda_forwarding, "owned_thread", owned)
    _prepared(monkeypatch, prompt=_PROMPT)

    start = await _start(
        _start_app(monkeypatch), channel_id="D1", channel_type="im", thread_ts="1.0", concierge=True
    )

    assert start["thread_id"] == _DM_THREAD
    cast(AsyncMock, mda_forwarding.claim_concierge).assert_not_awaited()
    cast(AsyncMock, mda_forwarding.join_concierge).assert_awaited_once_with("D1", "1.0", _DM_THREAD)


async def test_a_run_that_fails_to_come_together_is_reported_in_slack_not_a_500(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=_MDA_THREAD))
    failure = RuntimeError("metadata write failed")
    monkeypatch.setattr(slack_service, "prepare_slack_channel_run", AsyncMock(side_effect=failure))
    notify = AsyncMock()
    monkeypatch.setattr(slack_service, "notify_slack_processing_error", notify)

    with caplog.at_level("INFO"):
        start = await _start(_start_app(monkeypatch))

    assert start == _NO_RUN
    [request, repo, error] = notify.await_args.args
    assert (cast(SlackRequest, request).thread_id, repo, error) == (_MDA_THREAD, None, failure)
    [record] = [r for r in caplog.records if hasattr(r, "no_run_reason")]
    assert record.no_run_reason == "building the run failed"
    assert record.agent_thread_id == _MDA_THREAD


async def test_the_call_logs_why_it_starts_no_run(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=_MDA_THREAD))
    prepare = _prepared(monkeypatch)
    prepare.return_value = (None, None)

    with caplog.at_level("INFO"):
        start = await _start(_start_app(monkeypatch))

    assert start == _NO_RUN
    [record] = [r for r in caplog.records if hasattr(r, "no_run_reason")]
    assert record.no_run_reason == "no run prepared"


async def test_a_by_the_way_question_runs_on_a_thread_of_its_own(
    monkeypatch: pytest.MonkeyPatch, slack_api: SlackAPI
) -> None:
    monkeypatch.setattr(webhook_common, "resolve_slack_channel_context", _CHANNEL_CONTEXT)
    await SlackChannel.save_all({"id": "C1", "name": "engineering"})
    prepare = _prepared(monkeypatch)
    asked = slack_ask.AskChannelRun(
        input={"messages": [{"role": "user", "content": "why?"}]},
        configurable={"source": "slack", "github_login": "ada"},
    )
    prepare_ask = AsyncMock(return_value=asked)
    monkeypatch.setattr(slack_channel, "prepare_ask_channel_run", prepare_ask)

    start = await _start(
        _start_app(monkeypatch),
        kind="by_the_way",
        text="<@BOT> /btw why?",
        thread_ts="1.0",
        reply_thread_ts="1.0",
    )

    side = mda_forwarding.side_thread_id("C1", "1.2")
    assert start["thread_id"] == side
    assert start["interrupts"] is False
    assert start["run"]["input"] == asked.input
    assert verify_runtime_token(start["run"]["context"]["__run_token"]).thread_id == side
    # Whoever holds the Slack thread it was asked in: nothing is claimed but its own thread.
    cast(AsyncMock, mda_forwarding.claim_side).assert_awaited_once_with(side, owner_login="ada")
    cast(AsyncMock, mda_forwarding.claim_conversation).assert_not_awaited()
    prepare.assert_not_awaited()
    [question] = prepare_ask.await_args.args
    assert (question.question, question.thread_id, question.reply_thread_ts) == (
        "why?",
        side,
        "1.0",
    )
    assert slack_api.calls == []


async def test_a_web_breakout_runs_on_a_thread_of_its_own_and_its_thread_hears_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare = _prepared(monkeypatch)
    monkeypatch.setattr(
        webhook_common, "get_thread_workspace", AsyncMock(return_value="engineering")
    )
    notify = AsyncMock()
    monkeypatch.setattr(slack_channel, "notify_web_breakout", notify)
    app = _start_app(monkeypatch)
    body: dict[str, JsonValue] = {"kind": "web", "text": "<@BOT> /breakout:web why is CI red?"}

    first = await _start(app, **body)
    again = await _start(app, **body)

    side = mda_forwarding.side_thread_id("C1", "1.2")
    assert first["thread_id"] == again["thread_id"] == side
    request = prepare.await_args.args[0]
    assert (request.thread_id, request.web_only, request.text) == (side, True, "why is CI red?")
    assert (request.context_channel_id, request.context_thread_ts) == ("C1", "1.2")
    assert request.inherited_workspace == "engineering"
    # MDA asking again under the same webhook id gets the run, not a second notice.
    notify.assert_awaited_once()


@pytest.mark.parametrize("kind", ["by_the_way", "web"])
async def test_a_question_in_an_mda_conversation_branches_off_its_thread(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    prepare = _prepared(monkeypatch)
    asked = slack_ask.AskChannelRun(
        input={"messages": [{"role": "user", "content": "why?"}]},
        configurable={"source": "slack", "github_login": "ada"},
    )
    prepare_ask = AsyncMock(return_value=asked)
    monkeypatch.setattr(slack_channel, "prepare_ask_channel_run", prepare_ask)
    monkeypatch.setattr(slack_channel, "notify_web_breakout", AsyncMock())
    monkeypatch.setattr(
        webhook_common, "get_thread_workspace", AsyncMock(return_value="engineering")
    )
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=_MDA_THREAD))
    branches = AsyncMock(return_value=True)
    monkeypatch.setattr(forks, "branches_from", branches)
    text = "<@BOT> /btw why?" if kind == "by_the_way" else "<@BOT> /breakout:web why?"

    start = await _start(_start_app(monkeypatch), kind=kind, text=text)

    # A new thread the channel owns, which belongs to the one who asked and starts with
    # what the conversation's thread holds: the prompt leaves those Slack messages out.
    side = mda_forwarding.side_thread_id("C1", "1.2")
    branches.assert_awaited_once_with(_MDA_THREAD, login="ada")
    cast(AsyncMock, mda_forwarding.claim_side).assert_awaited_once_with(side, owner_login="ada")
    assert start["thread_id"] == side
    assert start["branches_conversation"] is True
    built = prepare_ask.await_args.args[0] if kind == "by_the_way" else prepare.await_args.args[0]
    assert (built.thread_id, built.held_thread_id) == (side, _MDA_THREAD)


async def test_a_question_branches_only_off_the_handlers_own_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_ask = AsyncMock(
        return_value=slack_ask.AskChannelRun(
            input={"messages": [{"role": "user", "content": "why?"}]},
            configurable={"source": "slack", "github_login": "ada"},
        )
    )
    monkeypatch.setattr(slack_channel, "prepare_ask_channel_run", prepare_ask)
    # A thread the handler can't read, such as a concierge DM's.
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=str(uuid.uuid4())))
    monkeypatch.setattr(forks, "branches_from", AsyncMock(return_value=True))

    start = await _start(_start_app(monkeypatch), kind="by_the_way", text="<@BOT> /btw why?")

    assert start["branches_conversation"] is False
    assert prepare_ask.await_args.args[0].held_thread_id == ""


@pytest.mark.parametrize("owner", [_MDA_THREAD, None], ids=["mda", "backend"])
async def test_a_breakout_opens_its_new_thread_and_runs_its_first_turn_there(
    monkeypatch: pytest.MonkeyPatch, owner: str | None
) -> None:
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=owner))
    backend_thread = str(uuid.uuid4())
    monkeypatch.setattr(
        webhook_common, "resolve_slack_thread_id", AsyncMock(return_value=backend_thread)
    )
    prepare = _prepared(monkeypatch)
    side = str(uuid.uuid4())
    opening = SlackRequest(channel_id="C2", thread_ts="200.0", thread_id=side)
    seen: list[tuple[SlackRequest, object]] = []

    async def breakout(
        request: SlackRequest,
        command: object,
        repo: object,
        *,
        hand_to_mda: Callable,
        branches: bool,
        tell: Callable,
    ) -> None:
        seen.append((request, command))
        assert branches is False  # Not readable here: seeded from Slack.
        await hand_to_mda(opening, None)

    monkeypatch.setattr(slack_channel, "process_slack_breakout", breakout)

    start = await _start(
        _start_app(monkeypatch), kind="breakout", text="<@BOT> /breakout fix the flaky test"
    )

    [(source, command)] = seen
    # Seeded from the Slack thread it was typed in, whoever holds it: nothing is claimed.
    assert source.thread_id == (owner or backend_thread)
    assert cast(slack_channel.BreakoutCommand, command).instruction == "fix the flaky test"
    cast(AsyncMock, mda_forwarding.claim_conversation).assert_not_awaited()
    cast(AsyncMock, slack_service.account_link_needed).assert_awaited_once_with("U1")
    assert prepare.await_args.args[0] is opening
    assert start["thread_id"] == side


async def test_a_breakout_that_opens_nothing_starts_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=_MDA_THREAD))
    prepare = _prepared(monkeypatch)
    monkeypatch.setattr(slack_channel, "process_slack_breakout", AsyncMock())

    with caplog.at_level("INFO"):
        start = await _start(
            _start_app(monkeypatch), kind="breakout", text="<@BOT> /breakout #nowhere"
        )

    assert start == _NO_RUN
    prepare.assert_not_awaited()
    [record] = [r for r in caplog.records if hasattr(r, "no_run_reason")]
    assert record.no_run_reason == "breakout opened nothing"


async def test_a_breakout_that_opens_nothing_says_why_in_the_thread_once_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=_MDA_THREAD))
    prepare = _prepared(monkeypatch)
    why = "I can't post in <#C2>. Add me to that channel, then try again."

    async def breakout(
        request: SlackRequest,
        command: object,
        repo: object,
        *,
        hand_to_mda: Callable,
        branches: bool,
        tell: Callable,
    ) -> None:
        await tell(request, why)

    monkeypatch.setattr(slack_channel, "process_slack_breakout", breakout)
    app = _start_app(monkeypatch)
    text = "<@BOT> /breakout <#C2|ops> fix the tests"

    first = await _start(app, kind="breakout", text=text)
    again = await _start(app, kind="breakout", text=text)

    # Not privately to the sender: MDA's handler replies in the Slack thread with it.
    assert first == _NO_RUN | {
        "declined": {"reason": "breakout", "settings_url": None, "message": why}
    }
    # MDA sends a call again under its webhook ID: the thread heard it already.
    assert again == _NO_RUN
    prepare.assert_not_awaited()


@pytest.mark.parametrize(("channel", "marker"), [("", True), ("C2", "C2")])
async def test_a_move_runs_on_the_conversation_and_moves_it_first(
    monkeypatch: pytest.MonkeyPatch, channel: str, marker: str | bool
) -> None:
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=_MDA_THREAD))
    _prepared(monkeypatch)

    start = await _start(
        _start_app(monkeypatch),
        kind="move",
        text="<@BOT> /breakout <#C2|eng>" if channel else "<@BOT> /breakout",
        move_channel_id=channel,
    )

    [message] = start["run"]["input"]["messages"]
    # MDA's run moves the thread before it calls the model.
    assert message["additional_kwargs"] == {SLACK_MOVE_MARKER: marker}


async def test_a_move_outside_an_mda_conversation_starts_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    prepare = _prepared(monkeypatch)

    with caplog.at_level("INFO"):
        start = await _start(_start_app(monkeypatch), kind="move", text="<@BOT> /breakout")

    assert start == _NO_RUN
    prepare.assert_not_awaited()
    cast(AsyncMock, mda_forwarding.claim_conversation).assert_not_awaited()
    cast(AsyncMock, slack_service.account_link_needed).assert_awaited_once_with("U1")
    [record] = [r for r in caplog.records if hasattr(r, "no_run_reason")]
    assert record.no_run_reason == "move outside an MDA conversation"


def _dm(**event: JsonValue) -> dict[str, JsonValue]:
    return _mention(type="message", channel="D1", channel_type="im", text="fix it", **event)


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
        "reason": "MDA's Slack channel doesn't take a concierge DM's edits",
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
    "payload",
    [
        {"type": "message_action", "callback_id": "open_swe_explain", "user": {"id": "U1"}},
        {
            "type": "view_submission",
            "user": {"id": "U1"},
            "view": {"callback_id": "human_review_decline", "private_metadata": "{}"},
        },
        {
            "type": "block_actions",
            "actions": [{"action_id": "open_swe_option_select_review", "type": "button"}],
        },
        {
            "type": "block_actions",
            "actions": [{"action_id": "open_swe_option_select_accept", "type": "button"}],
        },
    ],
    ids=[
        "explain this",
        "backend-opened modal",
        "review signup link",
        "review acceptance link",
    ],
)
async def test_explain_this_and_the_backends_modals_stay_here(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, JsonValue]
) -> None:
    act = AsyncMock(return_value={})
    monkeypatch.setattr(slack_routes, "act_on_interaction", act)

    _, tasks, _ = await _interact(monkeypatch, payload)

    assert tasks.tasks == []
    act.assert_awaited_once()


async def test_a_click_slack_delivers_here_is_acted_on_after_slack_is_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acted: list[str] = []

    async def act(interaction: object, _raw: str, tasks: BackgroundTasks, **_: object) -> dict:
        tasks.add_task(acted.append, cast(slack_routes.SlackInteraction, interaction).user.id)
        return {}

    monkeypatch.setattr(slack_routes, "act_on_interaction", act)
    response, tasks, _ = await _interact(
        monkeypatch, _click({"type": "expedited_review", "action": "approve", "fingerprint": "r1"})
    )

    assert response == {"status": "accepted", "message": "Slack click queued"}
    assert acted == []
    [(act_here, args)] = tasks.tasks
    await cast(Callable[..., Awaitable[None]], act_here)(*args)
    assert acted == ["U1"]


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
        "branches_conversation": False,
        "declined": None,
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


_COMMAND_TS = "1786573369.551099"


@pytest.mark.parametrize("text", ["<@BOT> /breakout", "<@BOT> fix it"])
async def test_any_message_in_an_mda_conversation_starts_nothing_here(
    monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    # MDA's channel tells a move from a turn: the backend starts neither for it.
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=_MDA_THREAD))
    notify = AsyncMock()
    monkeypatch.setattr(slack_service, "notify_slack_processing_error", notify)

    response, tasks, _request = await _webhook(_mention(text=text))

    assert cast(dict[str, str], response)["status"] == "ignored"
    [(report, args)] = tasks.tasks
    await cast(Callable[..., Awaitable[None]], report)(*args)
    assert cast(SlackRequest, notify.await_args.args[0]).thread_id == _MDA_THREAD


@pytest.mark.parametrize("text", ["<@BOT> /breakout", "<@BOT> /breakout:web"])
async def test_moving_a_backend_thread_or_an_empty_web_breakout_stays_on_the_backend(
    monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    tasks = _FakeBackgroundTasks()

    await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_mention(text=text), _SLACK_HEADERS)),
        cast(BackgroundTasks, tasks),
    )

    [(task, _args)] = tasks.tasks
    assert task in {slack_routes.process_slack_breakout, slack_routes.process_slack_web}
    assert tasks.kwargs == [{}]


async def test_a_breakout_into_a_channel_slack_doesnt_know_is_answered_by_the_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=_MDA_THREAD))
    response, tasks, _request = await _webhook(_mention(text="<@BOT> /breakout #nowhere"))

    assert response == {"status": "accepted", "message": "Slack breakout queued"}
    [(task, (_request, command, _repo))] = tasks.tasks
    assert task is slack_routes.process_slack_breakout
    assert (command.channel, command.channel_id) == ("#nowhere", "")


async def test_an_edit_in_an_mda_conversation_reaches_the_agent_as_a_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=_MDA_THREAD))
    prepare = _prepared(monkeypatch, content="help, fixed")

    start = await _start(
        _start_app(monkeypatch),
        kind="edit",
        text="<@BOT> help, fixed",
        message_ts="1.2",
        event_ts="1.9",
    )

    assert start["run"]["input"] == {"messages": [{"role": "user", "content": "help, fixed"}]}
    request = prepare.await_args.args[0]
    # The builder notes the correction for a run in progress, or makes it a turn.
    assert request.message_update
    assert (request.original_message_ts, request.event_ts) == ("1.2", "1.9")
    assert request.thread_id == _MDA_THREAD
    assert prepare.await_args.kwargs["read"] is None
    cast(AsyncMock, mda_forwarding.claim_conversation).assert_not_awaited()


async def test_an_edit_outside_an_mda_conversation_or_eligible_channel_starts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare = _prepared(monkeypatch)
    app = _start_app(monkeypatch)

    assert await _start(app, kind="edit", text="<@BOT> help, fixed") == _NO_RUN

    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=_MDA_THREAD))

    async def external(_channel_id: str, *, use_cache: bool = True) -> SlackChannelContext:
        return SlackChannelContext(is_ext_shared=not use_cache, is_pending_ext_shared=False)

    monkeypatch.setattr(webhook_common, "resolve_slack_channel_context", external)
    assert await _start(app, msg_id="msg_delivery-2", kind="edit", text="x") == _NO_RUN
    prepare.assert_not_awaited()
    cast(AsyncMock, mda_forwarding.claim_conversation).assert_not_awaited()


def _reaction(reaction: str, *, removed: bool = False) -> dict[str, JsonValue]:
    """A reaction as MDA's Slack channel POSTs it to its endpoint."""
    payload: dict[str, JsonValue] = {
        "type": "reaction_removed" if removed else "reaction_added",
        "user": "U1",
        "reaction": reaction,
        "item": {"type": "message", "channel": "C1", "ts": "1.2"},
        "event_ts": "1.9",
    }
    return {
        "event": {
            "key": "reaction",
            "deliveryId": "delivery-7",
            "payload": payload,
            "channelId": "C1",
            "messageTs": "1.2",
            "threadTs": "1.2",
            "text": "",
            "userId": "U1",
            "reaction": reaction,
            "removed": removed,
        },
        "thread": {"id": _MDA_THREAD, "channel": {"name": "slack"}},
    }


@pytest.mark.parametrize("removed", [False, True])
async def test_a_feedback_reaction_is_recorded_as_the_webhook_records_it(
    monkeypatch: pytest.MonkeyPatch, removed: bool
) -> None:
    from openswe.mda_channels import slack_channel_events  # noqa: PLC0415

    added, taken_back = AsyncMock(), AsyncMock()
    monkeypatch.setattr(slack_channel_events, "process_slack_reaction_added", added)
    monkeypatch.setattr(slack_channel_events, "process_slack_reaction_removed", taken_back)
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SERVICE_SECRET)
    app = FastAPI()
    app.include_router(slack_channel_events.router)
    reaction = next(iter(slack_channel_events.FEEDBACK_REACTIONS))

    answered = await _post_endpoint(
        app, _reaction(reaction, removed=removed), signed=True, path="/mda/slack/reactions"
    )

    assert answered.status_code == 200
    assert answered.json() is None
    recorded = taken_back if removed else added
    other = added if removed else taken_back
    recorded.assert_awaited_once()
    assert recorded.await_args.args[1] == "delivery-7"
    assert recorded.await_args.args[0]["reaction"] == reaction
    other.assert_not_awaited()


async def test_the_summon_reaction_tags_open_swe_and_any_other_does_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.mda_channels import slack_channel_events  # noqa: PLC0415

    summon = AsyncMock()
    monkeypatch.setattr(slack_channel_events, "process_slack_summon_reaction", summon)
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SERVICE_SECRET)
    app = FastAPI()
    app.include_router(slack_channel_events.router)

    for body in (
        _reaction("robin"),
        _reaction("robin", removed=True),
        _reaction("openswe"),
        _reaction("tada"),
    ):
        answered = await _post_endpoint(app, body, signed=True, path="/mda/slack/reactions")
        assert answered.status_code == 200

    summon.assert_awaited_once()
    event = summon.await_args.args[0]
    assert (event.item.channel, event.item.ts) == ("C1", "1.2")
    assert summon.await_args.args[1] == "delivery-7"
    assert summon.await_args.kwargs["bot_user_id"] == "BOT"
    unsigned = await _post_endpoint(
        app, _reaction("tada"), signed=False, path="/mda/slack/reactions"
    )
    assert unsigned.status_code == 401
