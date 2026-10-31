"""The verified Slack user who sent a message: named by Trigger Server, never guessed."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest

from managed_deepagents import SlackActor, SlackEvent, SlackMessageEvent, channels
from managed_deepagents._agent_auth_http import CredentialStoreError
from managed_deepagents._channels.http import endpoint as endpoint_module
from managed_deepagents._channels.slack.events import endpoint_fields
from managed_deepagents._channels.trigger import http as trigger_http
from managed_deepagents._channels.trigger.http import (
    TriggerMessageBackground,
    TriggerMessageDecision,
)
from managed_deepagents._channels.trigger.ingress import source_thread_id
from managed_deepagents._channels.trigger.types import trigger_event_actor
from tests.test_trigger_message_smoke import (
    _IDENTITY_ISSUER,
    _IDENTITY_KEY,
    _IDENTITY_PRINCIPAL_ID,
)


async def _source_thread(*, source_thread_key: str, **_: str) -> str:
    return source_thread_id(source_thread_key)


_THREAD = {
    "id": "592ec030-de34-503e-ab05-718e208a213d",
    "source_thread_key": "slack:v1:A1:T1:C1:171.1",
}
_MENTION: dict[str, Any] = {
    "type": "app_mention",
    "user": "U-FORGED",
    "text": "<@UBOT> deploy web",
    "ts": "171.2",
    "thread_ts": "171.1",
    "channel": "C1",
}


def _token() -> str:
    return jwt.encode(
        {
            "version": "1",
            "sub": _IDENTITY_PRINCIPAL_ID,
            "aud": "mda",
            "iss": _IDENTITY_ISSUER,
            "exp": int(time.time()) + 300,
            "principal": {"id": _IDENTITY_PRINCIPAL_ID, "kind": "person"},
        },
        _IDENTITY_KEY,
        algorithm="EdDSA",
        headers={"typ": "identity-context+jwt"},
    )


def test_reads_the_slack_user_trigger_server_names() -> None:
    actor = {"provider": "slack", "issuer": "T9EXTERNAL", "subject": "U1"}

    assert trigger_event_actor({"actor": actor}) == SlackActor(  # type: ignore[typeddict-item]
        team_id="T9EXTERNAL", id="U1"
    )


@pytest.mark.parametrize(
    "actor",
    [
        None,
        {"provider": "teams", "issuer": "T1", "subject": "U1"},
        {"provider": "slack", "issuer": "T1"},
        {"provider": "slack", "issuer": " ", "subject": "U1"},
        "U1",
    ],
    ids=["absent", "other-provider", "no-user", "blank-team", "not-an-object"],
)
def test_has_no_actor_for_a_missing_or_malformed_one(actor: object) -> None:
    assert trigger_event_actor({"actor": actor}) is None  # type: ignore[typeddict-item]


class _Request:
    def __init__(self) -> None:
        self.headers = {"x-service-key": "present"}


def _handle(token: str, actor: object = None, **kwargs: Any) -> Any:
    envelope = {
        "version": "1",
        "type": "message",
        "event_id": "Ev1",
        "delivery_id": "delivery-1",
        "created_at": "2026-10-20T12:00:00Z",
        "channel_id": "4f431bf2-19ed-45a9-8e00-2d1e7567f64b",
        "source_thread_key": _THREAD["source_thread_key"],
        "address": {"conversation_id": "C1", "thread_id": "171.1"},
        "message": {"id": "171.2", "text": _MENTION["text"]},
        "input": {"messages": [{"type": "human", "content": "deploy web"}]},
        # The raw event names someone else; only Trigger Server's actor counts.
        "raw_event": {**_MENTION, "actor": {"id": "U-FORGED"}},
        "identity_context": token,
        **({"actor": actor} if actor is not None else {}),
    }
    return asyncio.run(
        trigger_http.handle_trigger_channel_event_request(
            _Request(),
            raw_body=json.dumps(envelope).encode(),
            source_provider="slack",
            channel_name="support",
            assistant_id="agent",
            trigger_client=object(),  # type: ignore[arg-type]
            identity_context_key=_IDENTITY_KEY.public_key(),
            identity_context_issuer=_IDENTITY_ISSUER,
            conversation_thread=_source_thread,
            schedule=lambda _work: None,
            **kwargs,
        )
    )


def test_hands_trigger_servers_slack_user_to_the_decision_and_never_logs_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    seen: list[object] = []
    resolved: list[object] = []

    async def decide(_event: object, _thread: object, actor: object) -> TriggerMessageDecision:
        seen.append(actor)
        return TriggerMessageDecision("skipped")

    def resolve_principal(identity: object) -> str:
        resolved.append(identity)
        return _IDENTITY_PRINCIPAL_ID

    actor = {"provider": "slack", "issuer": "T9EXTERNAL", "subject": "U-VERIFIED"}
    with caplog.at_level(logging.DEBUG):
        response = _handle(
            _token(), actor, decide_message=decide, resolve_principal=resolve_principal
        )

    assert response.status_code == 202
    assert seen == [SlackActor(team_id="T9EXTERNAL", id="U-VERIFIED")]
    # The principal is keyed on the install team, not the guest's own team.
    assert resolved == [{"provider": "slack", "issuer": "T1", "subject": "U-VERIFIED"}]
    assert "U-VERIFIED" not in caplog.text


def _unavailable(_identity: object) -> str:
    raise CredentialStoreError("Agent Auth unavailable")


@pytest.mark.parametrize(
    "resolve_principal",
    [lambda _identity: "44444444-4444-4444-8444-444444444444", _unavailable],
    ids=["another-principal", "unavailable"],
)
def test_drops_an_actor_agent_auth_does_not_key_on_the_principal(
    resolve_principal: Any, caplog: pytest.LogCaptureFixture
) -> None:
    seen: list[object] = []

    async def decide(_event: object, _thread: object, actor: object) -> TriggerMessageDecision:
        seen.append(actor)
        return TriggerMessageDecision("skipped")

    actor = {"provider": "slack", "issuer": "T1", "subject": "U-FORGED"}
    with caplog.at_level(logging.WARNING):
        response = _handle(
            _token(), actor, decide_message=decide, resolve_principal=resolve_principal
        )

    assert response.status_code == 202
    assert seen == [None]
    assert "channel actor" in caplog.text
    assert "U-FORGED" not in caplog.text


def test_has_no_actor_when_trigger_server_names_none() -> None:
    seen: list[object] = []

    async def decide(_event: object, _thread: object, actor: object) -> TriggerMessageDecision:
        seen.append(actor)
        return TriggerMessageDecision("skipped")

    _handle(_token(), decide_message=decide)

    assert seen == [None]


def _decide_message(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> Any:
    captured: dict[str, Any] = {}

    async def handle(_request: object, **options: object) -> object:
        captured.update(options)
        return object()

    monkeypatch.setattr(trigger_http, "handle_trigger_channel_event_request", handle)
    asyncio.run(channels.slack(**kwargs).events({"name": "support", "request": object()}))
    return captured["decide_message"]


def _delivery() -> dict[str, object]:
    return {
        "delivery_id": "delivery-1",
        "source_thread_key": _THREAD["source_thread_key"],
        "address": {"conversation_id": "C1", "thread_id": "171.1"},
        "message": {"id": "171.2", "text": _MENTION["text"]},
        "input": {"messages": [{"type": "human", "content": "deploy web"}]},
        "raw_event": _MENTION,
    }


def test_gives_a_handler_the_verified_actor_and_keeps_it_out_of_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[SlackEvent] = []

    def mention(event: SlackEvent, _thread: object) -> str:
        seen.append(event)
        return "deploy web"

    decide = _decide_message(monkeypatch, on_event={"mention": mention})
    actor = SlackActor(team_id="T1", id="U-VERIFIED")
    decision = asyncio.run(decide(_delivery(), dict(_THREAD), actor))

    [event] = seen
    assert event.actor == actor
    assert event.user_id == "U-FORGED"
    assert isinstance(decision, TriggerMessageDecision)
    assert decision.input is not None
    assert "metadata" not in decision.input
    assert "U-VERIFIED" not in json.dumps(decision.input)


def test_sends_the_endpoint_the_verified_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    monkeypatch.setattr(
        endpoint_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    decide = _decide_message(
        monkeypatch, on_event={"mention": "https://backend.example/slack/runs"}
    )
    actor = SlackActor(team_id="T1", id="U1")
    routed = asyncio.run(decide(_delivery(), dict(_THREAD), actor))
    assert isinstance(routed, TriggerMessageBackground)
    asyncio.run(routed.decide())

    [request] = seen
    assert json.loads(request.content)["event"]["actor"] == {
        "provider": "slack",
        "id": "U1",
        "teamId": "T1",
    }


def test_leaves_actor_out_of_the_endpoint_fields_when_trigger_server_names_none() -> None:
    event = SlackMessageEvent(
        key="mention",
        delivery_id="d-1",
        text="hi",
        channel_id="C1",
        message_ts="1",
        thread_ts="1",
        payload={},
    )

    assert "actor" not in endpoint_fields(event)


def test_the_package_imports_without_the_identity_context_verifier() -> None:
    # The verifier needs PyJWT, which isn't a dependency of the package; it
    # loads with the first delivery. (Optional MCP extras may load PyJWT
    # themselves, so the check is on the verifier.)
    script = (
        "import sys, managed_deepagents\n"
        "from managed_deepagents import SlackActor, channels\n"
        "channels.slack(on_event={'mention': lambda e, t: None})\n"
        "assert 'managed_deepagents._channels.trigger.identity_context' not in sys.modules\n"
    )
    result = subprocess.run(
        [sys.executable, "-W", "ignore", "-c", script],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_finishes_a_message_with_the_verified_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    finished: list[tuple[Any, Any]] = []

    async def on_finish(target: Any, delivery: Any) -> None:
        finished.append((target, delivery))

    async def decide(_event: object, _thread: object, _actor: object) -> TriggerMessageDecision:
        return TriggerMessageDecision("skipped")

    work: list[Any] = []
    envelope = {
        "version": "1",
        "type": "message",
        "event_id": "Ev1",
        "delivery_id": "delivery-1",
        "created_at": "2026-10-20T12:00:00Z",
        "channel_id": "4f431bf2-19ed-45a9-8e00-2d1e7567f64b",
        "source_thread_key": _THREAD["source_thread_key"],
        "address": {"conversation_id": "C1", "thread_id": "171.1"},
        "input": {"messages": [{"type": "human", "content": "hi"}]},
        "raw_event": _MENTION,
        "identity_context": _token(),
        "actor": {"provider": "slack", "issuer": "T1", "subject": "U-VERIFIED"},
    }
    asyncio.run(
        trigger_http.handle_trigger_channel_event_request(
            _Request(),
            raw_body=json.dumps(envelope).encode(),
            source_provider="slack",
            channel_name="support",
            assistant_id="agent",
            trigger_client=object(),  # type: ignore[arg-type]
            identity_context_key=_IDENTITY_KEY.public_key(),
            identity_context_issuer=_IDENTITY_ISSUER,
            conversation_thread=_source_thread,
            schedule=work.append,
            decide_message=decide,
            on_finish=on_finish,
            resolve_principal=lambda _identity: _IDENTITY_PRINCIPAL_ID,
        )
    )
    for job in work:
        asyncio.run(job())

    [(_target, delivery)] = finished
    assert delivery["raw_event"]["actor"] == {
        "provider": "slack",
        "team_id": "T1",
        "id": "U-VERIFIED",
    }
    assert "identity_context" not in delivery["raw_event"]["delivery"]

    # channels.slack hands it to the author's on_finish as SlackEvent.actor.
    seen: list[SlackEvent] = []

    async def author(event: SlackEvent, _thread: object, _finished: object) -> None:
        seen.append(event)

    captured: dict[str, Any] = {}

    async def handle(_request: object, **options: object) -> object:
        captured.update(options)
        return object()

    monkeypatch.setattr(trigger_http, "handle_trigger_channel_event_request", handle)
    asyncio.run(channels.slack(on_finish=author).events({"name": "support", "request": object()}))
    asyncio.run(captured["on_finish"]({}, delivery))
    [event] = seen
    assert event.actor == SlackActor(team_id="T1", id="U-VERIFIED")
