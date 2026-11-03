"""MDA's Slack channel asks the backend what each message starts, and how it starts."""

import asyncio
import uuid
from typing import cast

import pytest
import rt.slack as slack
from managed_deepagents import ChannelRun, SlackChannelThread, SlackMessageEvent
from managed_deepagents._channels.http.types import ChannelName
from managed_deepagents._channels.run_input import RunChannel, normalize_run_inputs
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

    assert asked == [
        PlanSlackTurn(
            thread_id=uuid.UUID(_THREAD.id), message_ts="171.2", channel_id="C1", thread_ts="171.1"
        )
    ]
    assert result == [
        {
            "input": _RUN.input,
            "context": _RUN.context,
            "metadata": _RUN.metadata,
            "multitask_strategy": strategy,
            "webhook": _RUN.webhook,
        }
    ]
    # A list of one run, as a dict alone would be state: MDA starts it on the conversation.
    [run] = normalize_run_inputs(
        result, RunChannel(name="slack", provider="slack", conversation_thread_id=_THREAD.id)
    )
    assert "thread" not in run


def test_a_message_the_backend_starts_nothing_for_starts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _backend(monkeypatch, SlackTurnStart())

    assert asyncio.run(slack.start_turn(_EVENT, _THREAD)) is None


def test_a_concierge_dms_turn_runs_on_the_dms_one_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dm_thread = uuid.uuid4()
    _backend(monkeypatch, SlackTurnStart(run=_RUN, interrupts=True, thread_id=dm_thread))

    result = asyncio.run(slack.start_turn(_EVENT, _THREAD))

    assert isinstance(result, list)
    [run] = cast(list[ChannelRun], result)
    # The agent answers in Slack itself, so the run posts nothing.
    assert run.get("thread") == {"mode": "persistent", "id": str(dm_thread)}
    assert "auto_post" not in run
    assert run.get("multitask_strategy") == "interrupt"
    [normalized] = normalize_run_inputs(
        result, RunChannel(name="slack", provider="slack", conversation_thread_id=_THREAD.id)
    )
    assert normalized.get("thread") == {"mode": "persistent", "id": str(dm_thread)}


def test_the_channel_asks_its_handlers_for_every_message_and_click() -> None:
    from channels.slack import channel  # noqa: PLC0415
    from rt.slack_clicks import start_click  # noqa: PLC0415

    handlers = channel.on_event or {}
    assert channel.multitask_strategy == "interrupt"
    # The agent answers with Open SWE's Slack tools: the channel posts nothing itself.
    assert channel.auto_post is False
    assert set(handlers) == {"mention", "directMessage", "message", "interaction"}
    assert all(handlers[key] is slack.start_turn for key in ("mention", "directMessage", "message"))
    assert handlers["interaction"] is start_click
