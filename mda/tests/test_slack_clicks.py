"""MDA's Slack channel decides who may click the agent's buttons, and the backend builds the run."""

import asyncio
import dataclasses
import json
import uuid
from typing import Any, Literal, cast

import pytest
import rt.slack_clicks as clicks
from managed_deepagents import SlackActor, SlackChannelThread, SlackInteractionEvent
from managed_deepagents._channels.http.types import ChannelName
from pydantic import BaseModel
from runtime_api.endpoint import Endpoint
from runtime_api.slack import (
    PLAN_SLACK_CLICK,
    OptionClick,
    PlanSlackClick,
    RetryClick,
    SlackClickStart,
    SlackTurnRun,
    WorkflowPushClick,
)

_THREAD = SlackChannelThread(
    id=str(uuid.uuid4()),
    source_thread_key="slack:v1:A1:T1:C1:171.1",
    channel=ChannelName(name="slack"),
)
_RUN = SlackTurnRun(
    input={"messages": [{"role": "human", "content": "Option B"}]},
    context={"__run_token": "signed"},
    metadata={"channel": "slack"},
)
_RESPONSE_URL = "https://hooks.slack.com/actions/T1/1/abc"
_BUTTONS = {
    "type": "actions",
    "elements": [
        {"type": "button", "action_id": "open_swe_option_select_0", "text": {"text": "Option A"}},
        {"type": "button", "action_id": "open_swe_option_select_1", "text": {"text": "Option B"}},
    ],
}
_MESSAGE = {
    "ts": "171.5",
    "text": "Which one?",
    "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "Which one?"}}, _BUTTONS],
}


def _options(result: object) -> dict[str, Any]:
    """The one run a handler started, with its options: a list, as a dict alone would be state."""
    assert isinstance(result, list)
    [run] = cast(list[dict[str, Any]], result)
    return run


def _click(
    value: dict[str, Any], *, clicker: str = "U1", response_url: str = _RESPONSE_URL
) -> SlackInteractionEvent:
    return SlackInteractionEvent(
        delivery_id="delivery-1",
        action_id="open_swe_option_select_1",
        channel_id="C1",
        thread_ts="171.1",
        message_ts="171.5",
        value=json.dumps(value),
        user_id=clicker,
        actor=SlackActor(id=clicker, team_id="T1"),
        payload={
            "type": "block_actions",
            "team": {"id": "T1"},
            "response_url": response_url,
            "message": _MESSAGE,
            "actions": [
                {
                    "action_id": "open_swe_option_select_1",
                    "action_ts": "171.6",
                    "text": {"type": "plain_text", "text": "Option B"},
                }
            ],
        },
    )


class _Slack:
    """The backend and Slack's ``response_url``, in the order the handler used them."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, start: SlackClickStart) -> None:
        self.calls: list[tuple[str, Any]] = []

        async def backend(
            endpoint: Endpoint[BaseModel, BaseModel], request: BaseModel, *, msg_id: str
        ) -> BaseModel:
            assert endpoint is PLAN_SLACK_CLICK
            assert msg_id == "msg_delivery-1"
            self.calls.append(("backend", request))
            return start

        async def respond(_click: object, body: dict[str, object]) -> None:
            self.calls.append(("slack", body))

        monkeypatch.setattr(clicks, "ask_backend", backend)
        monkeypatch.setattr(clicks._Click, "_respond", respond)

    @property
    def asked(self) -> list[PlanSlackClick]:
        return [cast(PlanSlackClick, request) for kind, request in self.calls if kind == "backend"]

    @property
    def posted(self) -> list[dict[str, Any]]:
        return [body for kind, body in self.calls if kind == "slack"]


def _option(user_id: str | None = "U1") -> dict[str, Any]:
    return {"type": "open_swe_option", "response": "Option B", "user_id": user_id}


def test_an_option_answers_for_the_person_it_was_for(monkeypatch: pytest.MonkeyPatch) -> None:
    slack = _Slack(monkeypatch, SlackClickStart(run=_RUN, interrupts=False))

    result = asyncio.run(clicks.start_click(_click(_option()), _THREAD))

    assert _options(result)["input"] == _RUN.input
    # A click waits behind the work in progress, as the person's message would.
    assert _options(result)["multitask_strategy"] == "enqueue"
    [asked] = slack.asked
    assert asked.click == OptionClick(text="Option B")
    assert (asked.user_id, asked.message_ts, asked.action_ts, asked.team_id) == (
        "U1",
        "171.5",
        "171.6",
        "T1",
    )
    [replaced] = slack.posted
    assert replaced["replace_original"] is True
    assert replaced["blocks"] == [
        _MESSAGE["blocks"][0],
        {"type": "context", "elements": [{"type": "plain_text", "text": "Selected: Option B"}]},
    ]


def test_an_option_for_someone_else_starts_nothing_and_tells_only_the_clicker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slack = _Slack(monkeypatch, SlackClickStart(run=_RUN))

    result = asyncio.run(clicks.start_click(_click(_option("U1"), clicker="U2"), _THREAD))

    assert result is None
    assert slack.asked == []
    [told] = slack.posted
    assert told["response_type"] == "ephemeral"
    assert told["replace_original"] is False


def test_a_second_click_on_a_message_starts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    slack = _Slack(monkeypatch, SlackClickStart(acted=False))

    assert asyncio.run(clicks.start_click(_click(_option()), _THREAD)) is None
    assert slack.posted == []


def test_a_click_without_a_message_thread_starts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slack = _Slack(monkeypatch, SlackClickStart(run=_RUN))
    # As a click in a code channel's view comes: no message, no thread.
    click = dataclasses.replace(
        _click(_option()), thread_ts=None, message_ts=None, view_id="V1", code_channel=True
    )

    assert asyncio.run(clicks.start_click(click, _THREAD)) is None
    assert slack.calls == []


def test_a_concierge_dms_click_runs_on_the_dms_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    dm_thread = uuid.uuid4()
    _Slack(monkeypatch, SlackClickStart(run=_RUN, thread_id=dm_thread))

    result = asyncio.run(clicks.start_click(_click(_option()), _THREAD))

    assert _options(result)["thread"] == {"mode": "persistent", "id": str(dm_thread)}


@pytest.mark.parametrize(
    ("action", "status", "starts"),
    [("approve", "approved", True), ("reject", "rejected", False)],
)
def test_a_workflow_push_is_decided_and_an_approval_retries_the_push(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    status: Literal["approved", "rejected"],
    starts: bool,
) -> None:
    start = SlackClickStart(run=_RUN if starts else None, workflow_push=status)
    slack = _Slack(monkeypatch, start)
    value: dict[str, Any] = {
        "type": "workflow_push_approval",
        "action": action,
        "fingerprint": "f1",
    }

    result = asyncio.run(clicks.start_click(_click(value, clicker="U9"), _THREAD))

    assert (result is not None) is starts
    [asked] = slack.asked
    assert asked.click == WorkflowPushClick(fingerprint="f1", approved=action == "approve")
    [replaced] = slack.posted
    assert replaced["blocks"][-1]["elements"][0]["text"].startswith(f"Workflow push {status}")


def test_a_workflow_push_the_backend_no_longer_has_tells_the_clicker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slack = _Slack(monkeypatch, SlackClickStart(workflow_push=None))
    value: dict[str, Any] = {
        "type": "workflow_push_approval",
        "action": "approve",
        "fingerprint": "f1",
    }

    assert asyncio.run(clicks.start_click(_click(value), _THREAD)) is None
    [told] = slack.posted
    assert told["response_type"] == "ephemeral"


def _retry(user_id: str = "U1", **fields: object) -> dict[str, Any]:
    return {
        "type": "slack_retry",
        "channel_id": "C1",
        "thread_ts": "171.1",
        "user_id": user_id,
        "message_ts": "171.2",
        "reply_thread_ts": "",
        "nonce": "n1",
        "expires_at": 0,
        **fields,
    }


def test_a_retry_removes_its_button_before_it_sends_the_request_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slack = _Slack(monkeypatch, SlackClickStart(run=_RUN, interrupts=True))
    retry_message = {
        **_MESSAGE,
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": "Something went wrong"}},
            {
                "type": "actions",
                "elements": [{"type": "button", "action_id": "open_swe_option_select_retry"}],
            },
        ],
    }
    event = _click(_retry())
    event.payload["message"] = retry_message

    result = asyncio.run(clicks.start_click(event, _THREAD))

    assert _options(result)["multitask_strategy"] == "interrupt"
    assert [kind for kind, _ in slack.calls] == ["slack", "backend"]
    assert slack.posted[0]["blocks"][-1]["elements"][0]["text"] == "Retrying…"
    assert slack.asked[0].click == RetryClick(thread_ts="171.1", message_ts="171.2")


@pytest.mark.parametrize(
    "value",
    [
        _retry(user_id="U2"),
        _retry(channel_id="C2"),
        _retry(expires_at=1.0),
    ],
    ids=["someone else's", "another channel's", "expired"],
)
def test_a_retry_only_its_requester_may_click_starts_nothing_otherwise(
    monkeypatch: pytest.MonkeyPatch, value: dict[str, Any]
) -> None:
    slack = _Slack(monkeypatch, SlackClickStart(run=_RUN))

    assert asyncio.run(clicks.start_click(_click(value), _THREAD)) is None
    assert slack.asked == []
    [told] = slack.posted
    assert told["response_type"] == "ephemeral"


def test_a_click_without_a_verified_clicker_starts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slack = _Slack(monkeypatch, SlackClickStart(run=_RUN))
    event = _click(_option())
    unverified = dataclasses.replace(event, actor=None)

    assert asyncio.run(clicks.start_click(unverified, _THREAD)) is None
    assert slack.calls == []


def test_only_slacks_response_url_is_posted_to() -> None:
    assert clicks._response_url(_RESPONSE_URL) == _RESPONSE_URL
    assert clicks._response_url("https://evil.example/actions") is None
    assert clicks._response_url("http://hooks.slack.com/actions") is None
