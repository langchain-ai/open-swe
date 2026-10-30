"""MDA's Slack channel asks the backend what each message starts, and how it starts."""

import asyncio
import uuid
from typing import cast

import pytest
import rt.slack as slack
from managed_deepagents import SlackChannelThread, SlackMessageEvent
from managed_deepagents._channels.http.types import ChannelName
from pydantic import BaseModel
from runtime_api.endpoint import Endpoint
from runtime_api.slack import PLAN_SLACK_TURN, PlanSlackTurn, SlackTurnRun, SlackTurnStart

_THREAD = SlackChannelThread(
    id=str(uuid.uuid4()),
    source_thread_key="slack:v1:A1:T1:C1:171.1",
    channel=ChannelName(name="slack"),
)
_EVENT = SlackMessageEvent(
    key="message",
    delivery_id="delivery-1",
    text="and the tests too",
    channel_id="C1",
    message_ts="171.2",
    thread_ts="171.1",
    payload={},
)
_RUN = SlackTurnRun(
    input={"messages": [{"role": "human", "content": "and the tests too"}]},
    context={"__run_token": "signed"},
    metadata={"channel": "slack"},
    webhook="https://backend.example/webhooks/run-complete",
)


def _backend(monkeypatch: pytest.MonkeyPatch, start: SlackTurnStart) -> list[PlanSlackTurn]:
    asked: list[PlanSlackTurn] = []

    async def backend(
        endpoint: Endpoint[BaseModel, BaseModel], request: BaseModel, *, msg_id: str
    ) -> BaseModel:
        assert endpoint is PLAN_SLACK_TURN
        assert msg_id == "msg_delivery-1"
        asked.append(cast(PlanSlackTurn, request))
        return start

    monkeypatch.setattr(slack, "ask_backend", backend)
    return asked


@pytest.mark.parametrize(("interrupts", "strategy"), [(True, "interrupt"), (False, "enqueue")])
def test_a_message_redirects_the_work_in_progress_or_waits_as_the_backend_says(
    monkeypatch: pytest.MonkeyPatch, interrupts: bool, strategy: str
) -> None:
    asked = _backend(monkeypatch, SlackTurnStart(run=_RUN, interrupts=interrupts))

    result = asyncio.run(slack.start_turn(_EVENT, _THREAD))

    assert asked == [PlanSlackTurn(thread_id=uuid.UUID(_THREAD.id), message_ts="171.2")]
    assert result == (
        _RUN.input,
        {
            "context": _RUN.context,
            "metadata": _RUN.metadata,
            "multitask_strategy": strategy,
            "webhook": _RUN.webhook,
        },
    )


def test_a_message_the_backend_starts_nothing_for_starts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _backend(monkeypatch, SlackTurnStart())

    assert asyncio.run(slack.start_turn(_EVENT, _THREAD)) is None


def test_the_channel_asks_its_handler_for_every_message() -> None:
    from channels.slack import channel  # noqa: PLC0415

    assert channel.multitask_strategy == "interrupt"
    assert set(channel.on_event or {}) == {"mention", "directMessage", "message"}
    assert all(handler is slack.start_turn for handler in (channel.on_event or {}).values())
