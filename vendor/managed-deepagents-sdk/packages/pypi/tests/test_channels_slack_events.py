"""``channels.slack(on_event=...)``: what each Slack message starts."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage

from managed_deepagents import channels
from managed_deepagents._channels.slack import events as slack_events
from managed_deepagents._channels.slack.events import SlackEvent, SlackFile, SlackMessageEvent
from managed_deepagents._channels.trigger import http as trigger_http
from managed_deepagents._channels.trigger.http import TriggerMessageDecision
from managed_deepagents._channels.trigger.ingress import source_thread_id


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
    "files": [
        {"id": "F1", "name": "log.txt", "mimetype": "text/plain", "size": 12},
        {"name": "no id"},
    ],
}


def _delivery(raw_event: Mapping[str, object] | None) -> dict[str, object]:
    delivery: dict[str, object] = {
        "version": "1",
        "type": "message",
        "event_id": "Ev1",
        "delivery_id": "delivery-1",
        "created_at": "2026-10-20T12:00:00Z",
        "channel_id": "4f431bf2-19ed-45a9-8e00-2d1e7567f64b",
        "source_thread_key": _THREAD["source_thread_key"],
        "address": {"conversation_id": "C1", "thread_id": "171.1"},
        "message": {"id": "171.2", "text": "<@UBOT> deploy web"},
        "input": {"messages": [{"type": "human", "content": "deploy web"}]},
    }
    if raw_event is not None:
        delivery["raw_event"] = raw_event
    return delivery


@pytest.fixture
def slack_api(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Slack's ``auth.test`` names ``UBOT`` the app's bot user."""
    from managed_deepagents._channels.slack import api  # noqa: PLC0415
    from managed_deepagents._connections import connections  # noqa: PLC0415

    slack_events.reset_slack_bot_user()
    monkeypatch.setattr(connections, "get", AsyncMock(return_value="xoxb-token"))
    mock = AsyncMock(return_value={"ok": True, "user_id": "UBOT"})
    monkeypatch.setattr(api, "slack_api", mock)
    return mock


def _decider(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> tuple[dict[str, object], Any]:
    captured: dict[str, object] = {}

    async def handle(request: object, **options: object) -> object:
        captured.update(options)
        return object()

    monkeypatch.setattr(trigger_http, "handle_trigger_channel_event_request", handle)
    channel = channels.slack(**kwargs)
    asyncio.run(channel.events({"name": "support", "request": object()}))

    def decide(raw_event: Mapping[str, object] | None) -> TriggerMessageDecision | None:
        decide_message = captured["decide_message"]
        assert callable(decide_message)
        return asyncio.run(decide_message(_delivery(raw_event), dict(_THREAD), None))

    return captured, decide


def test_messages_start_as_before_without_on_event(monkeypatch: pytest.MonkeyPatch) -> None:
    captured, _ = _decider(monkeypatch)
    decide_message = captured["decide_message"]
    assert callable(decide_message)

    assert asyncio.run(decide_message(_delivery(_MENTION), dict(_THREAD), None)) is None
    assert captured["multitask_strategy"] == "enqueue"


def test_multitask_strategy_reaches_the_trigger_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured, _ = _decider(monkeypatch, multitask_strategy="reject")

    assert captured["multitask_strategy"] == "reject"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"on_event": {"app_mention": lambda _e, _t: None}}, "unsupported Slack event key"),
        ({"on_event": {"app_mention": None}}, "unsupported Slack event key"),
        ({"on_event": {"mention": 42}}, "must be callable"),
        ({"multitask_strategy": "queue"}, "multitask_strategy"),
    ],
)
def test_rejects_unknown_keys_handlers_and_strategies(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        channels.slack(**kwargs)  # type: ignore[arg-type]


def test_calls_the_kinds_handler_with_the_message_and_its_thread(
    monkeypatch: pytest.MonkeyPatch, slack_api: AsyncMock
) -> None:
    seen: list[tuple[SlackEvent, object]] = []

    def mention(event: SlackEvent, thread: object) -> str:
        seen.append((event, thread))
        return "deploy web"

    _, decide = _decider(monkeypatch, on_event={"mention": mention})
    decide(_MENTION)

    [(event, thread)] = seen
    assert event == SlackMessageEvent(
        key="mention",
        delivery_id="delivery-1",
        text="<@UBOT> deploy web",
        user_id="U1",
        channel_id="C1",
        message_ts="171.2",
        thread_ts="171.1",
        files=(SlackFile(id="F1", name="log.txt", mimetype="text/plain", size=12),),
        payload=_MENTION,
    )
    assert event.payload is not _MENTION
    assert thread.id == _THREAD["id"]  # type: ignore[attr-defined]
    assert thread.source_thread_key == _THREAD["source_thread_key"]  # type: ignore[attr-defined]
    assert thread.channel.name == "support"  # type: ignore[attr-defined]
    slack_api.assert_not_called()


def test_tells_mentions_direct_messages_and_other_messages_apart(
    monkeypatch: pytest.MonkeyPatch, slack_api: AsyncMock
) -> None:
    seen: list[str] = []

    def record(event: SlackEvent, _thread: object) -> None:
        seen.append(event.key)

    _, decide = _decider(
        monkeypatch, on_event={"mention": record, "directMessage": record, "message": record}
    )
    decide(_MENTION)
    decide({"type": "message", "channel_type": "im", "channel": "D1", "ts": "1"})
    decide({"type": "message", "channel_type": "channel", "channel": "C1", "ts": "2", "text": "hi"})
    # Under trigger_mode "thread" and "all", Slack sends mentions as messages.
    decide(
        {
            "type": "message",
            "channel_type": "channel",
            "channel": "C1",
            "ts": "3",
            "text": "<@UBOT> hi",
        }
    )
    decide(
        {
            "type": "message",
            "channel_type": "channel",
            "channel": "C1",
            "ts": "4",
            "text": "<@UX> hi",
        }
    )

    assert seen == ["mention", "directMessage", "message", "mention", "message"]
    # The bot user is looked up once, and only for messages that mention someone.
    slack_api.assert_awaited_once_with("xoxb-token", "auth.test", {})


def test_counts_a_mention_as_a_message_when_the_bot_user_is_unknown(
    monkeypatch: pytest.MonkeyPatch, slack_api: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    slack_api.side_effect = RuntimeError("invalid_auth xoxb-secret")
    seen: list[str] = []

    def record(event: SlackEvent, _thread: object) -> None:
        seen.append(event.key)

    _, decide = _decider(monkeypatch, on_event={"message": record})
    with caplog.at_level(logging.WARNING):
        decide({"type": "message", "channel": "C1", "ts": "3", "text": "<@UBOT> hi"})

    assert seen == ["message"]
    assert "slack bot user lookup failed" in caplog.text
    assert "xoxb-secret" not in caplog.text


def test_starts_a_kind_without_a_handler_as_before(
    monkeypatch: pytest.MonkeyPatch, slack_api: AsyncMock
) -> None:
    _, decide = _decider(monkeypatch, on_event={"message": lambda _e, _t: None})

    assert decide(_MENTION) is None
    assert decide(None) is None


def test_starts_nothing_when_a_handler_returns_nothing(
    monkeypatch: pytest.MonkeyPatch, slack_api: AsyncMock
) -> None:
    async def skip(_event: SlackEvent, _thread: object) -> None:
        return None

    _, decide = _decider(monkeypatch, on_event={"mention": skip})

    assert decide(_MENTION) == TriggerMessageDecision("skipped")


def test_fails_a_delivery_whose_handler_raises(
    monkeypatch: pytest.MonkeyPatch, slack_api: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    def fail(_event: SlackEvent, _thread: object) -> str:
        raise RuntimeError("secret-token-123")

    _, decide = _decider(monkeypatch, on_event={"mention": fail})
    with caplog.at_level(logging.ERROR):
        assert decide(_MENTION) == TriggerMessageDecision("failed")

    assert "slack callback failed" in caplog.text
    assert "secret-token-123" not in caplog.text


@pytest.mark.parametrize(
    "options",
    [
        {"metadata": {"thread_id": "x"}},
        {"context": {"mda_channel": {}}},
        {"webhook": "https://u:p@example.com"},
    ],
)
def test_fails_a_delivery_whose_handler_sets_reserved_options(
    monkeypatch: pytest.MonkeyPatch, slack_api: AsyncMock, options: dict[str, object]
) -> None:
    _, decide = _decider(monkeypatch, on_event={"mention": lambda _e, _t: ("go", options)})

    assert decide(_MENTION) == TriggerMessageDecision("failed")


def test_starts_the_handlers_input_with_its_run_options(
    monkeypatch: pytest.MonkeyPatch, slack_api: AsyncMock
) -> None:
    def mention(event: SlackEvent, _thread: object) -> object:
        return (
            HumanMessage(event.text),
            {
                "context": {"token": "signed"},
                "metadata": {"slack_channel": event.channel_id},
                "webhook": "https://backend.example/runs",
            },
        )

    _, decide = _decider(monkeypatch, on_event={"mention": mention})
    decision = decide(_MENTION)

    assert decision is not None
    assert decision.type == "input"
    assert decision.input is not None
    [message] = decision.input["messages"]
    assert message["role"] == "human"
    assert message["content"] == "<@UBOT> deploy web"
    assert decision.input["context"] == {"token": "signed"}
    assert decision.input["metadata"] == {"slack_channel": "C1"}
    assert decision.input["webhook"] == "https://backend.example/runs"


def test_adds_the_messages_blocks_and_attachments_to_the_handlers_input(
    monkeypatch: pytest.MonkeyPatch, slack_api: AsyncMock
) -> None:
    _, decide = _decider(monkeypatch, on_event={"mention": lambda _e, _t: "Check the alert"})
    decision = decide(
        {**_MENTION, "attachments": [{"fields": [{"title": "Host", "value": "web-1"}]}]}
    )

    assert decision is not None
    assert decision.input is not None
    [message] = decision.input["messages"]
    content = message["content"]
    assert isinstance(content, str)
    assert content.startswith("Check the alert\n\n")
    assert content.endswith("<slack_message_content>\nHost: web-1\n</slack_message_content>")


def test_leaves_state_only_input_without_messages(
    monkeypatch: pytest.MonkeyPatch, slack_api: AsyncMock
) -> None:
    _, decide = _decider(monkeypatch, on_event={"mention": lambda _e, _t: {"task": "triage"}})
    decision = decide(
        {**_MENTION, "attachments": [{"fields": [{"title": "Host", "value": "web-1"}]}]}
    )

    assert decision == TriggerMessageDecision(
        "input", {"messages": [], "state": {"task": "triage"}}
    )


class _Request:
    def __init__(self) -> None:
        self.headers = {"x-service-key": "present"}


def _handle(**kwargs: Any) -> Any:
    from tests.test_trigger_message_smoke import (  # noqa: PLC0415
        _IDENTITY_ISSUER,
        _IDENTITY_KEY,
        _signed_identity_context,
    )

    envelope = {**_delivery(_MENTION), "identity_context": _signed_identity_context()}
    kwargs.setdefault("schedule", lambda _work: None)
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
            **kwargs,
        )
    )


def test_a_skipped_message_starts_nothing_and_answers_202() -> None:
    start = AsyncMock()
    scheduled: list[object] = []

    async def decide(_event: object, _thread: object, _actor: object) -> TriggerMessageDecision:
        return TriggerMessageDecision("skipped")

    response = _handle(start_agent=start, decide_message=decide, schedule=scheduled.append)

    assert response.status_code == 202
    assert json.loads(response.body) == {"status": "accepted", "delivery_id": "delivery-1"}
    start.assert_not_called()
    assert scheduled == []


def test_a_failed_decision_answers_the_opaque_start_failure() -> None:
    start = AsyncMock()

    async def decide(_event: object, _thread: object, _actor: object) -> TriggerMessageDecision:
        return TriggerMessageDecision("failed")

    response = _handle(start_agent=start, decide_message=decide)

    assert response.status_code == 500
    assert json.loads(response.body) == {"error": "channel run could not be started"}
    start.assert_not_called()


def test_starts_the_decided_input_with_its_run_options_and_strategy() -> None:
    start = AsyncMock(return_value={"thread_id": _THREAD["id"], "run_id": "run-1"})
    messages = [{"role": "human", "content": "deploy web"}]
    threads: list[object] = []

    async def decide(_event: object, thread: object, _actor: object) -> TriggerMessageDecision:
        threads.append(thread)
        return TriggerMessageDecision(
            "input",
            {
                "messages": messages,
                "state": {"task": "deploy"},
                "context": {"token": "signed"},
                "metadata": {"slack_channel": "C1"},
                "webhook": "https://backend.example/runs",
            },
        )

    response = _handle(start_agent=start, decide_message=decide, multitask_strategy="interrupt")

    assert response.status_code == 202
    assert threads == [_THREAD]
    kwargs = start.await_args.kwargs
    assert kwargs["messages"] == messages
    assert kwargs["state"] == {"task": "deploy"}
    assert kwargs["context"] == {"token": "signed"}
    assert kwargs["metadata"] == {"slack_channel": "C1"}
    assert kwargs["webhook"] == "https://backend.example/runs"
    assert kwargs["multitask_strategy"] == "interrupt"
    assert kwargs["binding"]["event"] == {"type": "message", "messages": messages}
    assert kwargs["binding"]["name"] == "support"


def test_a_busy_thread_keeps_its_run_under_reject() -> None:
    class _BusyError(Exception):
        status_code = 409

    start = AsyncMock(side_effect=_BusyError())
    scheduled: list[object] = []

    rejected = _handle(start_agent=start, multitask_strategy="reject", schedule=scheduled.append)
    enqueued = _handle(start_agent=start, schedule=scheduled.append)

    assert rejected.status_code == 202
    assert enqueued.status_code == 500
    assert scheduled == []


_BLOCK_ACTIONS: dict[str, Any] = {
    "type": "block_actions",
    "token": "legacy-verification-token",
    "user": {"id": "U2"},
    "channel": {"id": "C1"},
    "container": {
        "type": "message",
        "message_ts": "171.3",
        "channel_id": "C1",
        "thread_ts": "171.1",
    },
    "response_url": "https://hooks.slack.com/actions/T1/1/abc",
    "actions": [{"action_id": "quick_reply", "value": "Use staging"}],
}


def _click(raw_event: Mapping[str, object] = _BLOCK_ACTIONS) -> dict[str, object]:
    """A forwarded click on a button the agent posted with its own blocks."""
    return {
        "version": "1",
        "type": "user_prompt_response",
        "provider": "slack",
        "event_id": "1730.5",
        "delivery_id": "delivery-2",
        "created_at": "2026-10-20T12:01:00Z",
        "channel_id": "4f431bf2-19ed-45a9-8e00-2d1e7567f64b",
        "source_thread_key": _THREAD["source_thread_key"],
        "address": {"conversation_id": "C1", "thread_id": "171.1"},
        "interaction": {"action": "quick_reply", "value": "Use staging"},
        "raw_event": raw_event,
    }


def test_leaves_interactions_alone_without_an_interaction_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured, _ = _decider(monkeypatch, on_event={"mention": lambda _e, _t: None})
    decide_interaction = captured["decide_interaction"]
    assert callable(decide_interaction)

    assert asyncio.run(decide_interaction(_click(), dict(_THREAD), None)) is None


def test_calls_the_interaction_handler_with_the_click_without_slacks_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from managed_deepagents._channels.actor import SlackActor  # noqa: PLC0415
    from managed_deepagents._channels.slack.events import SlackInteractionEvent  # noqa: PLC0415

    seen: list[tuple[object, object]] = []

    def handler(event: Any, thread: Any) -> str:
        seen.append((event, thread))
        return str(event.value)

    captured, _ = _decider(monkeypatch, on_event={"interaction": handler})
    decide_interaction = captured["decide_interaction"]
    assert callable(decide_interaction)
    actor = SlackActor(id="U2", team_id="T1")

    decision = asyncio.run(decide_interaction(_click(), dict(_THREAD), actor))

    payload = {key: value for key, value in _BLOCK_ACTIONS.items() if key != "token"}
    event, thread = seen[0]
    assert event == SlackInteractionEvent(
        delivery_id="delivery-2",
        action_id="quick_reply",
        value="Use staging",
        user_id="U2",
        channel_id="C1",
        message_ts="171.3",
        thread_ts="171.1",
        payload=payload,
        actor=actor,
    )
    assert thread.id == _THREAD["id"]
    assert decision is not None
    assert decision.type == "input"
    assert decision.input is not None
    assert decision.input["messages"][0]["content"] == "Use staging"


def test_reads_a_modal_submission_which_has_no_message(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    def handler(event: Any, _thread: Any) -> None:
        seen.append(event)

    captured, _ = _decider(monkeypatch, on_event={"interaction": handler})
    decide_interaction = captured["decide_interaction"]
    assert callable(decide_interaction)
    submission = {"type": "view_submission", "user": {"id": "U2"}, "view": {"callback_id": "f"}}

    decision = asyncio.run(decide_interaction(_click(submission), dict(_THREAD), None))

    assert decision == TriggerMessageDecision("skipped")
    assert seen[0].message_ts is None
    assert (seen[0].channel_id, seen[0].thread_ts) == ("C1", "171.1")


def test_hands_a_finished_interaction_to_on_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    finished: list[tuple[Any, Any, Any]] = []

    async def on_finish(event: Any, thread: Any, how: Any) -> None:
        finished.append((event, thread, how))

    captured, _ = _decider(monkeypatch, on_finish=on_finish)
    finish = captured["on_finish"]
    assert callable(finish)

    asyncio.run(
        finish(
            {},
            {
                "thread_id": _THREAD["id"],
                "raw_event": {"delivery": _click()},
                "type": "skipped",
                "reason": "ignored",
                "interrupts": [],
            },
        )
    )

    event, thread, how = finished[0]
    assert (event.key, event.action_id, event.message_ts) == ("interaction", "quick_reply", "171.3")
    assert thread.source_thread_key == _THREAD["source_thread_key"]
    assert how["type"] == "skipped"


def test_keeps_a_thread_whose_mention_a_handler_declined_declined_in_thread_mode(
    monkeypatch: pytest.MonkeyPatch, slack_api: AsyncMock
) -> None:
    captured, _ = _decider(
        monkeypatch, trigger_mode="thread", on_event={"mention": lambda _e, _t: None}
    )
    only_continues_thread = captured["only_continues_thread"]
    assert callable(only_continues_thread)
    reply = {**_MENTION, "type": "message", "text": "and staging?"}

    assert asyncio.run(only_continues_thread(_delivery(reply))) is True
    assert asyncio.run(only_continues_thread(_delivery(_MENTION))) is False
    assert asyncio.run(only_continues_thread(_delivery({**reply, "channel_type": "im"}))) is False


def test_lets_a_reply_that_may_mention_the_agent_start_when_its_bot_user_is_unknown(
    monkeypatch: pytest.MonkeyPatch, slack_api: AsyncMock
) -> None:
    slack_api.side_effect = RuntimeError("invalid_auth")
    captured, _ = _decider(
        monkeypatch, trigger_mode="thread", on_event={"mention": lambda _e, _t: None}
    )
    only_continues_thread = captured["only_continues_thread"]
    assert callable(only_continues_thread)
    reply = {**_MENTION, "type": "message"}

    assert asyncio.run(only_continues_thread(_delivery(reply))) is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"trigger_mode": "thread"},
        {"trigger_mode": "thread", "on_event": {"directMessage": lambda _e, _t: None}},
        {"trigger_mode": "mention", "on_event": {"mention": lambda _e, _t: None}},
        {"trigger_mode": "all", "on_event": {"mention": lambda _e, _t: None}},
        {"on_event": {"mention": lambda _e, _t: None}},
    ],
    ids=["no-on-event", "no-mention-handler", "mention-mode", "all-mode", "no-mode"],
)
def test_lets_replies_start_as_before_outside_thread_mode_with_handlers(
    monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any]
) -> None:
    captured, _ = _decider(monkeypatch, **kwargs)

    assert captured["only_continues_thread"] is None


def test_warns_that_a_message_handler_never_runs_in_mention_mode() -> None:
    with pytest.warns(UserWarning, match="never called"):
        channels.slack(trigger_mode="mention", on_event={"message": lambda _e, _t: None})


@pytest.mark.parametrize(
    ("thread_exists", "starts"),
    [(False, 0), (True, 1), (None, 1)],
    ids=["never-ran", "ran", "lookup-fails"],
)
def test_a_message_that_only_continues_a_thread_starts_only_where_the_agent_ran(
    thread_exists: bool | None, starts: int
) -> None:
    start = AsyncMock(return_value=object())
    decide = AsyncMock(return_value=None)

    async def exists(**_kwargs: object) -> bool:
        if thread_exists is None:
            raise RuntimeError("thread lookup down")
        return thread_exists

    async def only_continues(_event: object) -> bool:
        return True

    response = _handle(
        start_agent=start,
        decide_message=decide,
        only_continues_thread=only_continues,
        thread_exists=exists,
        observe_agent=AsyncMock(),
    )

    assert response.status_code == 202
    assert start.await_count == starts
    assert decide.await_count == starts


def test_a_reply_in_a_thread_the_agent_never_ran_on_finishes_skipped() -> None:
    work: list[Any] = []
    on_finish = AsyncMock()

    async def only_continues(_event: object) -> bool:
        return True

    async def never_ran(**_kwargs: object) -> bool:
        return False

    _handle(
        start_agent=AsyncMock(),
        only_continues_thread=only_continues,
        thread_exists=never_ran,
        on_finish=on_finish,
        schedule=work.append,
    )
    for job in work:
        asyncio.run(job())

    on_finish.assert_awaited_once()
    finished = on_finish.await_args.args[1]
    assert (finished["type"], finished["reason"], finished["interrupts"]) == (
        "skipped",
        "ignored",
        [],
    )


def test_hands_on_finish_the_event_its_handler_got_not_a_fresh_reading(
    monkeypatch: pytest.MonkeyPatch, slack_api: AsyncMock
) -> None:
    seen: list[SlackEvent] = []
    finished: list[SlackEvent] = []

    def record(event: SlackEvent, _thread: object) -> None:
        seen.append(event)

    async def on_finish(event: SlackEvent, _thread: object, _finished: object) -> None:
        finished.append(event)

    captured, decide = _decider(
        monkeypatch, trigger_mode="all", on_event={"message": record}, on_finish=on_finish
    )
    reply = {**_MENTION, "type": "message"}
    # The bot user is unknown while the handler runs, so the message is a
    # ``message``; by the time it finishes, it would read as a ``mention``.
    slack_api.side_effect = [RuntimeError("invalid_auth"), {"ok": True, "user_id": "UBOT"}]
    decide(reply)
    slack_events.reset_slack_bot_user()
    finish = captured["on_finish"]
    assert callable(finish)
    asyncio.run(
        finish(
            {},
            {
                "thread_id": _THREAD["id"],
                "raw_event": {"delivery": _delivery(reply)},
                "type": "skipped",
                "reason": "ignored",
                "interrupts": [],
            },
        )
    )

    assert len(seen) == 1
    assert finished == seen
    assert finished[0] is seen[0]
    assert finished[0].key == "message"
