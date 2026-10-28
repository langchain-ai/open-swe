"""``channels.slack(on_event=...)`` endpoints: a service of yours decides what a message starts."""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import TypeAdapter

from managed_deepagents import SlackEndpointRequest, channels
from managed_deepagents._channels import run_input as run_input_module
from managed_deepagents._channels.http import endpoint as endpoint_module
from managed_deepagents._channels.trigger import channel as slack_channel_module
from managed_deepagents._channels.trigger import http as trigger_http
from managed_deepagents._channels.trigger.http import (
    TriggerMessageBackground,
    TriggerMessageDecision,
)
from managed_deepagents._channels.trigger.ingress import source_thread_id


async def _source_thread(*, source_thread_key: str, **_: str) -> str:
    return source_thread_id(source_thread_key)


_URL = "https://backend.example/slack/runs"
_THREAD = {
    "id": "592ec030-de34-503e-ab05-718e208a213d",
    "source_thread_key": "slack:v1:A1:T1:C1:171.1",
}
_MENTION: dict[str, Any] = {
    "type": "app_mention",
    "user": "U1",
    "text": "<@UBOT> deploy web",
    "ts": "171.2",
    "thread_ts": "171.1",
    "channel": "C1",
    "files": [{"id": "F1", "name": "log.txt"}],
}


def _delivery() -> dict[str, object]:
    return {
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
        "raw_event": _MENTION,
    }


def _service(
    monkeypatch: pytest.MonkeyPatch, answer: httpx.Response | Exception
) -> list[httpx.Request]:
    seen: list[httpx.Request] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(endpoint_module.httpx, "AsyncClient", client)
    monkeypatch.setattr(endpoint_module, "_RETRY_DELAYS_SECONDS", ())
    return seen


def _route(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> object:
    captured: dict[str, Any] = {}

    async def handle(_request: object, **options: object) -> object:
        captured.update(options)
        return object()

    monkeypatch.setattr(trigger_http, "handle_trigger_channel_event_request", handle)
    asyncio.run(channels.slack(**kwargs).events({"name": "support", "request": object()}))
    return asyncio.run(captured["decide_message"](_delivery(), dict(_THREAD), None))


def _decide(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> TriggerMessageDecision:
    routed = _route(monkeypatch, **kwargs)
    assert isinstance(routed, TriggerMessageBackground)
    return asyncio.run(routed.decide())


def test_asks_the_endpoint_after_trigger_servers_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _service(
        monkeypatch,
        httpx.Response(
            200,
            json={
                "input": "deploy web",
                "context": {"token": "signed"},
                "metadata": {"slack_channel": "C1"},
                "webhook": "https://backend.example/done",
            },
        ),
    )
    backend = httpx.Request("POST", _URL, headers={"x-api-key": "secret"})

    routed = _route(monkeypatch, on_event={"mention": backend})

    # Nothing is asked before the Trigger handler answers and runs it.
    assert isinstance(routed, TriggerMessageBackground)
    assert seen == []
    decision = asyncio.run(routed.decide())
    assert decision.type == "input"
    assert decision.input is not None
    assert decision.input["context"] == {"token": "signed"}
    assert decision.input["metadata"] == {"slack_channel": "C1"}
    assert decision.input["webhook"] == "https://backend.example/done"
    [request] = seen
    assert request.headers["x-api-key"] == "secret"
    # The same JSON the TypeScript runtime sends.
    assert json.loads(request.content) == {
        "event": {
            "key": "mention",
            "deliveryId": "delivery-1",
            "payload": _MENTION,
            "text": _MENTION["text"],
            "userId": "U1",
            "channelId": "C1",
            "messageTs": "171.2",
            "threadTs": "171.1",
            "files": [{"id": "F1", "name": "log.txt"}],
        },
        "thread": {"id": _THREAD["id"], "channel": {"name": "support"}},
    }
    TypeAdapter(SlackEndpointRequest).validate_python(json.loads(request.content))


def test_decides_a_handler_functions_message_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _route(monkeypatch, on_event={"mention": lambda _e, _t: None}) == (
        TriggerMessageDecision("skipped")
    )


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, content=b"null", headers={"content-type": "application/json"}),
        httpx.Response(204),
    ],
    ids=["null", "204"],
)
def test_starts_nothing_when_the_endpoint_answers_nothing(
    monkeypatch: pytest.MonkeyPatch, answer: httpx.Response
) -> None:
    _service(monkeypatch, answer)

    assert _decide(monkeypatch, on_event={"mention": _URL}) == TriggerMessageDecision("skipped")


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(400, text="no"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"prompt": "hi"}),
        httpx.ReadTimeout("timed out"),
    ],
    ids=["non-2xx", "not-json", "wrong-shape", "timeout"],
)
def test_starts_nothing_on_a_failed_answer_and_logs_it_safely(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    answer: httpx.Response | Exception,
) -> None:
    _service(monkeypatch, answer)

    with caplog.at_level(logging.ERROR):
        decision = _decide(monkeypatch, on_event={"mention": _URL})

    assert decision == TriggerMessageDecision("failed")
    assert "slack callback failed" in caplog.text
    assert "deploy web" not in caplog.text


@pytest.mark.parametrize(
    "url", ["/runs", "http://backend.example/runs", "https://u:p@backend.example/runs"]
)
def test_refuses_an_endpoint_it_cant_use_when_the_channel_is_declared(url: str) -> None:
    with pytest.raises(TypeError, match=r'Slack on_event\["mention"\] must be an absolute https'):
        channels.slack(on_event={"mention": url})


def test_takes_an_endpoint_for_any_slack_message_kind() -> None:
    backend = httpx.Request("POST", _URL, headers={"x-api-key": "secret"})
    channels.slack(on_event={"mention": backend, "directMessage": _URL, "message": _URL})


@pytest.mark.parametrize("key", ["mention", "directMessage", "message"])
def test_a_slack_endpoint_request_takes_each_key(key: str) -> None:
    request = TypeAdapter(SlackEndpointRequest).validate_python(
        {
            "event": {"key": key, "deliveryId": "d-1", "payload": {"type": "message"}},
            "thread": {"id": "t-1", "channel": {"name": "slack"}},
        }
    )
    assert request["event"]["key"] == key


class _Request:
    def __init__(self) -> None:
        self.headers = {"x-service-key": "present"}


def _handle(**kwargs: Any) -> Any:
    from tests.test_trigger_message_smoke import (  # noqa: PLC0415
        _IDENTITY_ISSUER,
        _IDENTITY_KEY,
        _signed_identity_context,
    )

    envelope = {**_delivery(), "identity_context": _signed_identity_context()}
    kwargs.setdefault("trigger_client", object())
    return asyncio.run(
        trigger_http.handle_trigger_channel_event_request(
            _Request(),
            raw_body=json.dumps(envelope).encode(),
            source_provider="slack",
            channel_name="support",
            assistant_id="agent",
            identity_context_key=_IDENTITY_KEY.public_key(),
            identity_context_issuer=_IDENTITY_ISSUER,
            conversation_thread=_source_thread,
            **kwargs,
        )
    )


def test_answers_trigger_server_before_a_background_decision() -> None:
    timeline: list[str] = []
    work: list[Any] = []

    async def start(**kwargs: Any) -> dict[str, str]:
        timeline.append("start")
        return {"thread_id": _THREAD["id"], "run_id": "run-1"}

    async def observe(**_kwargs: Any) -> dict[str, object]:
        timeline.append("observe")
        return {"status": "success", "values": {"messages": []}}

    async def decide() -> TriggerMessageDecision:
        timeline.append("decide")
        return TriggerMessageDecision("input", {"messages": [], "state": {"task": "go"}})

    async def route(_event: object, _thread: object, _actor: object) -> TriggerMessageBackground:
        return TriggerMessageBackground(decide)

    client = AsyncMock()
    response = _handle(
        start_agent=start,
        observe_agent=observe,
        decide_message=route,
        schedule=work.append,
        trigger_client=client,
    )

    assert response.status_code == 202
    assert timeline == []
    [job] = work
    asyncio.run(job())
    assert timeline == ["decide", "start", "observe"]


@pytest.mark.parametrize("outcome", ["skipped", "failed"])
def test_starts_nothing_for_a_background_decision_that_starts_none(
    outcome: str, caplog: pytest.LogCaptureFixture
) -> None:
    start = AsyncMock()
    work: list[Any] = []

    async def decide() -> TriggerMessageDecision:
        return TriggerMessageDecision(outcome)  # type: ignore[arg-type]

    async def route(_event: object, _thread: object, _actor: object) -> TriggerMessageBackground:
        return TriggerMessageBackground(decide)

    response = _handle(start_agent=start, decide_message=route, schedule=work.append)
    with caplog.at_level(logging.INFO):
        asyncio.run(work[0]())

    assert response.status_code == 202
    start.assert_not_called()
    assert "channel message started no run" in caplog.text


def test_logs_a_run_a_background_decision_couldnt_start(
    caplog: pytest.LogCaptureFixture,
) -> None:
    start = AsyncMock(side_effect=RuntimeError("agent server secret detail"))
    work: list[Any] = []

    async def decide() -> TriggerMessageDecision:
        return TriggerMessageDecision(
            "input", {"messages": [{"role": "human", "content": "go"}], "state": {}}
        )

    async def route(_event: object, _thread: object, _actor: object) -> TriggerMessageBackground:
        return TriggerMessageBackground(decide)

    _handle(start_agent=start, decide_message=route, schedule=work.append)
    with caplog.at_level(logging.ERROR):
        asyncio.run(work[0]())

    assert "channel run could not be started" in caplog.text
    assert "secret detail" not in caplog.text


def _slack_event() -> Any:
    return SimpleNamespace(key="message", delivery_id="delivery-1", payload={})


def test_logs_why_it_refused_a_slack_handler_run_input(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler() -> object:
        return ("go", {"metadata": {"user_id": "U-1"}})

    with caplog.at_level(logging.ERROR):
        decision = asyncio.run(slack_channel_module._run_handler(_slack_event(), handler))

    assert decision.type == "failed"
    assert (
        "slack handler returned invalid run input: run metadata key is reserved: user_id"
        in caplog.text
    )


def test_logs_only_the_type_of_a_handler_type_error(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def normalize(_value: object) -> object:
        raise TypeError("secret detail")

    monkeypatch.setattr(run_input_module, "normalize_run_input", normalize)
    with caplog.at_level(logging.ERROR):
        decision = asyncio.run(slack_channel_module._run_handler(_slack_event(), lambda: "go"))

    assert decision.type == "failed"
    assert "slack handler returned invalid run input (TypeError)" in caplog.text
    assert "secret detail" not in caplog.text
