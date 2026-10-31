"""``channels.slack(on_finish=...)``: a message is done, once, with how."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest

from managed_deepagents import channels
from managed_deepagents._channels.trigger import http as trigger_http
from managed_deepagents._channels.trigger.http import (
    TriggerMessageBackground,
    TriggerMessageDecision,
)
from managed_deepagents._channels.trigger.ingress import source_thread_id
from tests.test_trigger_message_smoke import (
    _IDENTITY_ISSUER,
    _IDENTITY_KEY,
    _signed_identity_context,
)


async def _source_thread(*, source_thread_key: str, **_: str) -> str:
    return source_thread_id(source_thread_key)


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
}


def _delivery(**extra: object) -> dict[str, object]:
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
        "identity_context": _signed_identity_context(),
        **extra,
    }


class _Request:
    def __init__(self) -> None:
        self.headers = {"x-service-key": "present"}


class _Client:
    """Records Trigger callbacks in the shared timeline."""

    def __init__(self, timeline: list[str]) -> None:
        self.timeline = timeline

    async def post_runtime_event(self, _channel_id: str, event: dict[str, Any]) -> dict[str, str]:
        self.timeline.append(event["type"])
        return {"status": "accepted"}

    async def post_action(self, _channel_id: str, _action: object) -> dict[str, str]:
        self.timeline.append("post")
        return {"id": "reply-1"}


def _deliver(
    observed: dict[str, object] | Exception | None = None,
    body: dict[str, object] | None = None,
    **kwargs: Any,
) -> tuple[Any, AsyncMock, list[str]]:
    """Run a message through the handler and its background work."""
    timeline: list[str] = []

    async def finish(*_args: object) -> None:
        timeline.append("finish")

    on_finish = AsyncMock(side_effect=finish)

    async def start(**_kwargs: object) -> dict[str, str]:
        return {"thread_id": _THREAD["id"], "run_id": "run-1"}

    async def observe(**_kwargs: object) -> object:
        if isinstance(observed, Exception):
            raise observed
        return observed or {
            "status": "success",
            "values": {"messages": [{"type": "ai", "content": "Done."}]},
        }

    work: list[Any] = []
    options: dict[str, Any] = {
        "start_agent": start,
        "observe_agent": observe,
        "on_finish": on_finish,
        "trigger_client": _Client(timeline),
        "schedule": work.append,
        "reactions": False,
        **kwargs,
    }
    response = asyncio.run(
        trigger_http.handle_trigger_channel_event_request(
            _Request(),
            raw_body=json.dumps(body or _delivery()).encode(),
            source_provider="slack",
            channel_name="support",
            assistant_id="agent",
            identity_context_key=_IDENTITY_KEY.public_key(),
            identity_context_issuer=_IDENTITY_ISSUER,
            conversation_thread=_source_thread,
            **options,
        )
    )
    for job in work:
        asyncio.run(job())
    return response, on_finish, timeline


def _finished(on_finish: AsyncMock) -> object:
    """How the one finished message finished, as ``http_channel``'s ``on_finish`` hears it."""
    on_finish.assert_awaited_once()
    target, delivery = on_finish.await_args.args
    assert target == {
        "channel_id": _delivery()["channel_id"],
        "address": _delivery()["address"],
        "source_thread_key": _THREAD["source_thread_key"],
    }
    finished = dict(delivery)
    assert finished.pop("thread_id") == _THREAD["id"]
    raw = finished.pop("raw_event")
    assert raw["delivery"]["delivery_id"] == "delivery-1"
    assert "identity_context" not in raw["delivery"]
    return finished


def test_finishes_a_run_that_answered_after_its_lifecycle_callbacks() -> None:
    _, on_finish, timeline = _deliver()

    assert _finished(on_finish) == {
        "type": "run",
        "run": {"id": "run-1", "status": "success"},
        "interrupts": [],
    }
    # The run posts its own answer (_channels/delivery.py).
    assert timeline == ["run.started", "run.completed", "finish"]


@pytest.mark.parametrize("status", ["error", "timeout"])
def test_finishes_a_run_that_ended_badly(status: str) -> None:
    _, on_finish, _ = _deliver({"status": status, "values": {}})

    assert _finished(on_finish) == {
        "type": "run",
        "run": {"id": "run-1", "status": status},
        "interrupts": [],
    }


def test_finishes_a_run_that_stopped_on_interrupts_with_what_it_waits_on() -> None:
    interrupt = {"id": "interrupt-1", "value": {"question": "Deploy?"}}
    _, on_finish, timeline = _deliver(
        {"status": "success", "values": {"__interrupt__": [interrupt]}}
    )

    assert _finished(on_finish) == {
        "type": "run",
        "run": {"id": "run-1", "status": "success"},
        "interrupts": [interrupt],
    }
    # The run reports its own pause (_channels/delivery.py).
    assert timeline == ["run.started", "finish"]


def test_finishes_a_run_that_was_stopped() -> None:
    # From Slack's Stop, Studio, or a newer message.
    _, on_finish, timeline = _deliver({"status": "interrupted", "values": {}})

    assert _finished(on_finish) == {
        "type": "run",
        "run": {"id": "run-1", "status": "interrupted"},
        "interrupts": [],
    }
    assert timeline == ["run.started", "run.cancelled", "finish"]


def test_finishes_a_run_mda_lost_track_of() -> None:
    _, on_finish, _ = _deliver(RuntimeError("gone"))

    assert _finished(on_finish) == {"type": "failed", "reason": "observe", "interrupts": []}


def test_finishes_a_message_its_handler_skipped() -> None:
    async def skip(_event: object, _thread: object, _actor: object) -> TriggerMessageDecision:
        return TriggerMessageDecision("skipped")

    _, on_finish, _ = _deliver(decide_message=skip)

    assert _finished(on_finish) == {"type": "skipped", "reason": "ignored", "interrupts": []}


def test_finishes_a_message_a_busy_thread_rejected() -> None:
    class _BusyError(Exception):
        status_code = 409

    _, on_finish, _ = _deliver(
        multitask_strategy="reject", start_agent=AsyncMock(side_effect=_BusyError())
    )

    assert _finished(on_finish) == {"type": "skipped", "reason": "rejected", "interrupts": []}


def _background(decided: TriggerMessageDecision) -> Any:
    async def decide() -> TriggerMessageDecision:
        return decided

    async def route(_event: object, _thread: object, _actor: object) -> TriggerMessageBackground:
        return TriggerMessageBackground(decide)

    return route


def test_finishes_a_background_message_whose_run_couldnt_start() -> None:
    response, on_finish, _ = _deliver(
        start_agent=AsyncMock(side_effect=RuntimeError("down")),
        decide_message=_background(
            TriggerMessageDecision(
                "input", {"messages": [{"role": "human", "content": "go"}], "state": {}}
            )
        ),
    )

    assert response.status_code == 202
    assert _finished(on_finish) == {"type": "failed", "reason": "start", "interrupts": []}


def test_finishes_a_background_message_its_endpoint_declined() -> None:
    _, on_finish, _ = _deliver(decide_message=_background(TriggerMessageDecision("failed")))

    assert _finished(on_finish) == {"type": "skipped", "reason": "ignored", "interrupts": []}


def test_doesnt_finish_a_message_whose_run_couldnt_start_before_trigger_servers_answer() -> None:
    async def fail(_event: object, _thread: object, _actor: object) -> TriggerMessageDecision:
        return TriggerMessageDecision("failed")

    failed, failed_finish, _ = _deliver(decide_message=fail)
    unstarted, unstarted_finish, _ = _deliver(start_agent=AsyncMock(side_effect=RuntimeError()))

    assert failed.status_code == 500
    assert unstarted.status_code == 500
    failed_finish.assert_not_awaited()
    unstarted_finish.assert_not_awaited()


def test_doesnt_finish_an_interrupt_resume() -> None:
    body = _delivery(
        type="interrupt_resume",
        interaction={"type": "interrupt_resume", "resume": {"interrupt-1": {"ok": True}}},
    )
    body.pop("input")
    _, on_finish, _ = _deliver(
        body=body,
        resume_agent=AsyncMock(return_value={"thread_id": _THREAD["id"], "run_id": "run-2"}),
    )

    on_finish.assert_not_awaited()


def test_logs_a_finish_that_raises_after_the_lifecycle_callbacks(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR):
        _, _, timeline = _deliver(on_finish=AsyncMock(side_effect=RuntimeError("hook secret")))

    assert timeline == ["run.started", "run.completed"]
    assert "channel finish notice failed" in caplog.text
    assert "hook secret" not in caplog.text


def test_hands_a_finished_message_to_on_finish_as_a_handler_gets_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def handle(_request: object, **options: object) -> object:
        captured.update(options)
        return object()

    monkeypatch.setattr(trigger_http, "handle_trigger_channel_event_request", handle)
    seen: list[tuple[Any, Any, Any]] = []

    async def on_finish(event: Any, thread: Any, finished: Any) -> None:
        seen.append((event, thread, finished))

    asyncio.run(
        channels.slack(on_finish=on_finish).events({"name": "support", "request": object()})
    )
    finished = {"type": "run", "run": {"id": "run-1", "status": "success"}, "interrupts": []}
    asyncio.run(
        captured["on_finish"](
            {}, {"thread_id": _THREAD["id"], "raw_event": {"delivery": _delivery()}, **finished}
        )
    )

    [(event, thread, got)] = seen
    assert (event.key, event.channel_id, event.message_ts) == ("mention", "C1", "171.2")
    assert (thread.id, thread.source_thread_key, thread.channel.name) == (
        _THREAD["id"],
        _THREAD["source_thread_key"],
        "support",
    )
    assert got == finished


def test_rejects_an_on_finish_that_isnt_callable() -> None:
    with pytest.raises(TypeError, match="on_finish"):
        channels.slack(on_finish="done")  # type: ignore[arg-type]
