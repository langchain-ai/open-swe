"""``/webhooks/slack`` hands MDA's conversations to MDA's Slack channel, and only those."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import BackgroundTasks, FastAPI
from standardwebhooks import Webhook
from starlette.datastructures import URL
from starlette.requests import Request

from openswe.slack import channel as slack_channel
from openswe.slack import events as slack_events
from openswe.slack import mda_forwarding
from openswe.slack import routes as slack_routes
from openswe.slack import webhook as slack_service
from openswe.slack.payloads import SlackChannelContext
from openswe.webhooks import common as webhook_common

_EVENTS_URL = "https://triggers.example/v1/triggers/webhooks/slack-custom-app/events"
_MDA_THREAD = str(uuid.uuid4())
_SERVICE_SECRET = "5e" * 32  # noqa: S105 - test value


class _FakeBackgroundTasks:
    def __init__(self) -> None:
        self.tasks: list[tuple[Any, tuple[Any, ...]]] = []

    def add_task(self, func: Any, *args: Any) -> None:
        self.tasks.append((func, args))


class _FakeRequest:
    def __init__(self, payload: dict[str, Any], headers: dict[str, str]) -> None:
        self.headers = headers
        self.url = URL("http://test/webhooks/slack")
        self._body = json.dumps(payload).encode()

    async def body(self) -> bytes:
        return self._body


def _mention() -> dict[str, Any]:
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
    monkeypatch.setenv("OPEN_SWE_SLACK_TRIGGER_EVENTS_URL", _EVENTS_URL)
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


async def test_a_conversation_for_mda_goes_to_trigger_server_exactly_as_slack_sent_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = AsyncMock(return_value=_MDA_THREAD)
    monkeypatch.setattr(mda_forwarding, "conversation_thread", conversation)
    request = _FakeRequest(_mention(), dict(_SLACK_HEADERS))
    tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, request), cast(BackgroundTasks, tasks)
    )

    assert response["status"] == "accepted"
    assert conversation.await_args.kwargs["root_ts"] == "1786573369.551099"
    [(task, (url, body, headers))] = tasks.tasks
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


async def test_every_other_conversation_stays_on_the_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mda_forwarding, "conversation_thread", AsyncMock(return_value=None))
    tasks = _FakeBackgroundTasks()

    response = await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(_mention(), dict(_SLACK_HEADERS))), cast(BackgroundTasks, tasks)
    )

    assert response["status"] == "accepted"
    [(task, (request, _repo))] = tasks.tasks
    assert task is slack_service.process_slack_mention
    assert request.thread_id == "backend-thread"


@pytest.mark.parametrize(
    ("scope", "user", "direct_message", "takes"),
    [
        ("off", "U-team", True, False),
        ("users", "U-team", False, True),
        ("users", "U-other", True, False),
        ("dms", "U-other", True, True),
        ("dms", "U-other", False, False),
        ("all", "U-other", False, True),
        ("bogus", "U-team", True, False),
    ],
)
def test_the_canary_takes_only_what_its_scope_names(
    monkeypatch: pytest.MonkeyPatch, scope: str, user: str, direct_message: bool, takes: bool
) -> None:
    monkeypatch.setenv("OPEN_SWE_SLACK_MDA_SCOPE", scope)
    monkeypatch.setenv("OPEN_SWE_SLACK_MDA_USERS", "U-team, U-two")

    assert (
        mda_forwarding.Canary.from_env().takes(user_id=user, direct_message=direct_message) is takes
    )


def test_without_a_trigger_server_url_nothing_goes_to_mda(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_SWE_SLACK_MDA_SCOPE", "all")
    monkeypatch.delenv("OPEN_SWE_SLACK_TRIGGER_EVENTS_URL")

    assert mda_forwarding.Canary.from_env().takes(user_id="U1", direct_message=True) is False


def test_mda_thread_ids_match_trigger_servers_thread_key() -> None:
    assert mda_forwarding.mda_thread_id("A1", "T1", "C1", "1.2") == str(
        uuid.uuid5(uuid.NAMESPACE_URL, "slack:v1:A1:T1:C1:1.2")
    )


async def _post_endpoint(app: FastAPI, body: dict[str, Any], signed: bool) -> httpx.Response:
    content = json.dumps(body).encode()
    headers = {"content-type": "application/json"}
    if signed:
        timestamp = datetime.now(tz=UTC)
        headers |= {
            "webhook-id": "msg_delivery-1",
            "webhook-timestamp": str(int(timestamp.timestamp())),
            "webhook-signature": Webhook(_SERVICE_SECRET).sign(
                "msg_delivery-1", timestamp, content.decode()
            ),
        }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post("/slack/channel/events", content=content, headers=headers)


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
