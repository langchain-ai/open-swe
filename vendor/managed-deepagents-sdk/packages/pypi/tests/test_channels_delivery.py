"""A run's result goes to its thread origin as the run's graph stream ends."""

from __future__ import annotations

import asyncio
import logging
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command, interrupt

from managed_deepagents._agent_auth_http import missing_workspace_connection_error
from managed_deepagents._channels import delivery
from managed_deepagents._channels.messaging_registry import register_channel_messaging
from managed_deepagents._channels.run_output import RunOutputMiddleware
from managed_deepagents._channels.runtime import require_text_content
from managed_deepagents._managed_thread import _verified_lead_metadata
from managed_deepagents._run_input import ManagedCompiledAgent
from managed_deepagents._thread_record import next_thread_record, thread_record_metadata

KEY = "slack:v1:A1:T1:C1:171.1"
TRIGGER: dict[str, Any] = {
    "name": "slack",
    "provider": "slack",
    "source_thread_key": KEY,
    "delivery_id": "delivery-1",
    "transport": "trigger_server",
    "event": {"type": "message", "messages": []},
    "target": {
        "channel_id": "channel-1",
        "address": {"conversation_id": "C1", "thread_id": "171.1"},
        "source_thread_key": KEY,
    },
}
DIRECT: dict[str, Any] = {
    **TRIGGER,
    "name": "orders",
    "provider": "orders",
    "source_thread_key": "http:v1:orders",
    "transport": "direct",
    "target": {"issue": "ENG-1"},
}
SLACK = {"mda_user_id": "alice", "mda_source_provider": "slack", "mda_source_thread_id": KEY}
ORDERS = {"mda_source_provider": "orders", "mda_source_thread_id": "http:v1:orders"}
STUDIO = {"identity": "studio"}
NOTE = {"deepagents_notification": {"task_id": "t1", "agent_name": "a", "status": "success"}}
NEW_KEY = "slack:v1:A1:T1:C1:171.9"
STARTED = {
    "channel_id": "channel-1",
    "address": {"conversation_id": "C1", "thread_id": "171.9"},
    "source_thread_key": NEW_KEY,
}
CRON_USER = {
    "identity": "alice",
    "permissions": [],
    "mda_user_id": "alice",
    "mda_agent_auth_principal_id": "alice",
    "mda_source_provider": "slack",
    "mda_source_thread_id": KEY,
    "mda_scheduled_run": True,
}


def _record(binding: dict[str, Any] = TRIGGER) -> dict[str, object]:
    return thread_record_metadata(next_thread_record(None, binding, "alice"))


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """What the run sends: Trigger actions and events, HTTP posts and paused notices."""
    out = SimpleNamespace(
        actions=[],
        events=[],
        posts=[],
        notice=AsyncMock(),
        fail=False,
        started=None,
        deployment_action=AsyncMock(return_value={"id": "171.9", "thread": STARTED}),
        hand_off=AsyncMock(),
    )

    def record(sent: list[object]) -> Any:
        async def send(*args: Any, **request: Any) -> dict[str, object]:
            if out.fail and sent is not out.events:
                raise RuntimeError("provider is down")
            sent.append(request or args[-1])
            return {"id": "posted", **({"thread": out.started} if out.started else {})}

        return send

    client = SimpleNamespace(
        post_action=record(out.actions),
        post_runtime_event=record(out.events),
        post_deployment_action=out.deployment_action,
    )
    monkeypatch.setattr(delivery, "create_trigger_client", lambda: client)
    monkeypatch.setattr(delivery, "hand_off_scheduled_thread", out.hand_off)
    monkeypatch.setattr(delivery, "tracer_session_id", AsyncMock(return_value=None))
    http = SimpleNamespace(post=record(out.posts))
    register_channel_messaging(
        [
            SimpleNamespace(name="slack", kind="slack"),
            SimpleNamespace(
                name="orders", kind="orders", messaging=lambda _: http, paused_notice=out.notice
            ),
        ]
    )
    return out


def _texts(sent: SimpleNamespace) -> list[str]:
    return [action["message"]["text"] for action in sent.actions]


#: The (empty) block OpenAI's Responses API, through LangChain, puts before the answer's text.
REASONING = {"type": "reasoning", "id": "rs_1", "reasoning": ""}


def _agent() -> Any:
    """Answers each message; ``approve?`` waits on an interrupt; ``boom``, ``missing`` raise.

    ``think`` answers with a reasoning block before its text.
    """

    def answer(state: MessagesState) -> dict[str, object]:
        text = state["messages"][-1].text
        if text == "boom":
            raise RuntimeError("secret detail")
        if text == "missing":
            raise missing_workspace_connection_error("clickup")
        if text == "[quiet]":
            return {"messages": [AIMessage(text)]}
        if text == "approve?":
            text = f"approved {interrupt({'question': 'ok?'})}"
        if text == "think":
            return {"messages": [AIMessage([REASONING, {"type": "text", "text": "re: think"}])]}
        return {"messages": [AIMessage(f"re: {text}")]}

    graph = (
        StateGraph(MessagesState)
        .add_node("answer", answer)
        .add_edge(START, "answer")
        .add_edge("answer", END)
        .compile(checkpointer=InMemorySaver())
    )
    graph.__class__ = ManagedCompiledAgent
    return graph


def _stateless_agent() -> Any:
    """Answers each message without a checkpointer, recording the answer as the middleware does."""

    def answer(state: MessagesState) -> dict[str, object]:
        return {"messages": [AIMessage(f"re: {state['messages'][-1].text}")]}

    def record(state: MessagesState) -> None:
        RunOutputMiddleware().after_agent(state, None)  # ty: ignore[invalid-argument-type]

    graph = (
        StateGraph(MessagesState)
        .add_node("answer", answer)
        .add_node("record", record)
        .add_edge(START, "answer")
        .add_edge("answer", "record")
        .add_edge("record", END)
        .compile()
    )
    graph.__class__ = ManagedCompiledAgent
    return graph


def _config(user: dict[str, Any], *, run_id: str = "run-1", **extra: Any) -> dict[str, Any]:
    configurable = {"thread_id": "t1", "run_id": run_id, "langgraph_auth_user": user}
    return {"metadata": extra.pop("metadata", {}), "configurable": {**configurable, **extra}}


def _user(text: str, **extra: object) -> dict[str, object]:
    return {"messages": [{"role": "user", "content": text, **extra}]}


def test_a_channel_run_posts_its_answer(sent: SimpleNamespace) -> None:
    _agent().invoke(_user("hi"), _config(SLACK), context={"mda_channel": TRIGGER})
    _agent().invoke(_user("hi"), _config(ORDERS), context={"mda_channel": DIRECT})

    # A retried run posts under the same action id, so Trigger posts it once.
    assert sent.actions == [
        {
            "type": "post",
            "action_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "mda-reply:run-1")),
            "address": {"conversation_id": "C1", "thread_id": "171.1"},
            "source_thread_key": KEY,
            "message": {"text": "re: hi"},
        }
    ]
    assert sent.posts == [
        {"message": {"type": "content", "content": "re: hi"}, "target": {"issue": "ENG-1"}}
    ]


def test_a_text_only_channel_gets_the_text_of_an_answer_with_reasoning(
    sent: SimpleNamespace,
) -> None:
    _agent().invoke(_user("think"), _config(ORDERS), context={"mda_channel": DIRECT})

    # Text-only transports such as Linear reject any block that isn't text.
    assert [require_text_content(post["message"]) for post in sent.posts] == ["re: think"]


@pytest.mark.parametrize(
    ("user", "binding", "metadata", "fails"),
    [
        (SLACK, TRIGGER, {}, True),
        (ORDERS, DIRECT, {}, False),
        ({**ORDERS, "mda_scheduled_run": True}, DIRECT, {"mda_managed": True}, True),
    ],
    ids=["trigger-fails-the-run", "http-keeps-the-run", "scheduled-http-fails-the-run"],
)
def test_failed_reply_policies(
    sent: SimpleNamespace, user: Any, binding: Any, metadata: Any, fails: bool
) -> None:
    sent.fail = True
    config = _config(user, metadata={**_record(binding), **metadata})
    if fails:
        with pytest.raises(RuntimeError, match="provider is down"):
            _agent().invoke(_user("hi"), config)
    else:
        assert _agent().invoke(_user("hi"), config)["messages"][-1].text == "re: hi"


def test_a_failed_reply_logs_the_channel_and_why(
    sent: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    sent.fail = True
    register_channel_messaging([SimpleNamespace(name="mute", kind="orders")])
    with caplog.at_level(logging.ERROR, logger="mda"):
        _agent().invoke(_user("hi"), _config(ORDERS), context={"mda_channel": DIRECT})
        _agent().invoke(
            _user("hi"),
            _config(ORDERS, thread_id="t2"),
            context={"mda_channel": {**DIRECT, "name": "mute"}},
        )

    assert [record.getMessage() for record in caplog.records] == [
        "[mda] channel reply failed (channel=orders): RuntimeError",
        (
            "[mda] channel reply failed (channel=mute): "
            "NoReplyTransportError: the channel has no `post`, "
            "so it cannot post the run's answer. "
            'Add `post` to the channel, or set `reply` to "none"'
        ),
    ]


def test_a_woken_runs_resume_posts_only_its_own_answers(sent: SimpleNamespace) -> None:
    # deepagents wakes the lead with a run of its own: no delivery, only the record.
    graph = _agent()
    graph.invoke(_user("question"), _config(SLACK), context={"mda_channel": TRIGGER})
    woken = _config(SLACK, run_id="run-2", metadata=_record())
    graph.invoke(_user("approve?", **NOTE), woken)
    graph.invoke(Command(resume="yes"), _config(SLACK, run_id="run-3", metadata=_record()))

    assert _texts(sent) == ["re: question", "re: approved yes"]


def test_a_studio_resume_from_a_named_checkpoint_posts_its_answer(sent: SimpleNamespace) -> None:
    graph = _agent()
    graph.invoke(_user("approve?"), _config(SLACK), context={"mda_channel": TRIGGER})
    paused = graph.get_state(_config(SLACK)).config["configurable"]
    sent.events.clear()
    resumed = _config(
        STUDIO, run_id="run-2", metadata=_record(), checkpoint_id=paused["checkpoint_id"]
    )
    asyncio.run(graph.ainvoke(Command(resume="yes"), resumed))

    assert _texts(sent) == ["re: approved yes"]
    assert sent.events == []


def test_a_quiet_answer_is_not_posted(sent: SimpleNamespace) -> None:
    _agent().invoke(_user("[quiet]"), _config(SLACK), context={"mda_channel": TRIGGER})
    assert sent.actions == []


def test_a_channel_that_leaves_replying_to_the_agent_gets_no_answer(
    sent: SimpleNamespace,
) -> None:
    register_channel_messaging(
        [
            SimpleNamespace(name="slack", kind="slack", reply="none"),
            SimpleNamespace(name="orders", kind="orders", reply="none"),
        ]
    )
    _agent().invoke(_user("hi"), _config(SLACK), context={"mda_channel": TRIGGER})
    _agent().invoke(_user("hi"), _config(ORDERS), context={"mda_channel": DIRECT})
    assert sent.actions == []
    assert sent.posts == []

    # Only the answer is the agent's: a pause is still reported.
    _agent().invoke(_user("approve?"), _config(SLACK), context={"mda_channel": TRIGGER})
    assert [event["type"] for event in sent.events] == ["run.interrupted"]


def test_a_paused_run_shows_its_interrupts_in_the_trigger_thread(sent: SimpleNamespace) -> None:
    _agent().invoke(_user("approve?"), _config(SLACK), context={"mda_channel": TRIGGER})

    assert sent.actions == []
    ((body),) = sent.events
    assert body["type"] == "run.interrupted"
    assert (body["run_thread_id"], body["run_id"], body["source_thread_key"]) == (
        "t1",
        "run-1",
        KEY,
    )
    assert [item["value"] for item in body["interrupts"]] == [{"question": "ok?"}]


def test_a_paused_http_run_posts_its_channels_notice(sent: SimpleNamespace) -> None:
    # Also for a run no ingress observer watches, here a Studio resume.
    _agent().invoke(_user("approve?"), _config(STUDIO, metadata=_record(DIRECT), assistant_id="a"))

    sent.notice.assert_awaited_once_with(
        {"issue": "ENG-1"}, {"thread_id": "t1", "assistant_id": "a"}
    )


@pytest.mark.parametrize(
    ("binding", "text"),
    [(TRIGGER, "approve?"), (TRIGGER, "hi"), (DIRECT, "approve?")],
    ids=["trigger-pause", "answer", "http-pause"],
)
def test_an_async_subagent_reports_its_pause_to_its_leads_origin(
    sent: SimpleNamespace, binding: Any, text: str
) -> None:
    config = _config(SLACK, run_id="run-9", deepagents_parent={"thread_id": "lead"})
    config["configurable"]["thread_id"] = "helper"
    token = _verified_lead_metadata.set(("lead", _record(binding)))
    try:
        asyncio.run(_agent().ainvoke(_user(text), config))
    finally:
        _verified_lead_metadata.reset(token)

    # A subagent never posts its answer; its pause links its own thread.
    assert (sent.actions, sent.posts) == ([], [])
    if binding is DIRECT:
        sent.notice.assert_awaited_once_with({"issue": "ENG-1"}, {"thread_id": "helper"})
    elif text == "approve?":
        ((body),) = sent.events
        assert (body["run_thread_id"], body["source_thread_key"]) == ("helper", KEY)
    else:
        assert sent.events == []


@pytest.mark.parametrize("new_thread", [False, True])
def test_a_scheduled_run_posts_to_its_threads_origin(
    sent: SimpleNamespace, new_thread: bool
) -> None:
    metadata = {**_record(), "mda_managed": True, "mda_schedule_spec": {"new_thread": new_thread}}
    user = {**SLACK, "mda_scheduled_run": True}
    _agent().invoke(_user("daily"), _config(user, metadata=metadata))

    ((action),) = sent.actions
    # A new-thread schedule starts a new provider thread each time.
    assert ("thread_id" in action["address"]) is not new_thread
    assert ("source_thread_key" in action) is not new_thread


def _new_thread_cron() -> dict[str, object]:
    return {**_record(), "mda_managed": True, "mda_schedule_spec": {"new_thread": True}}


def test_a_new_thread_schedule_opens_its_thread_with_its_answer_and_hands_it_off(
    sent: SimpleNamespace,
) -> None:
    sent.started = STARTED
    _agent().invoke(_user("hi"), _config(CRON_USER, metadata=_new_thread_cron()))

    action_id = delivery.reply_action_id("run-1")
    assert sent.actions == [
        {
            "type": "post",
            "action_id": action_id,
            "address": {"conversation_id": "C1"},
            "follow_thread": True,
            "message": {"text": "re: hi"},
        }
    ]
    ((handoff, metadata),) = [call.args for call in sent.hand_off.await_args_list]
    assert handoff == {
        "thread": "t1",
        "execution": CRON_USER,
        "conversation": {"provider": "slack", "source_thread_key": NEW_KEY},
    }
    assert metadata["ls_mda_conversation"] == NEW_KEY
    assert metadata["ls_mda_participants"] == ["alice"]
    channel = metadata["ls_mda_thread"]["origin"]["channel"]
    assert channel["source_thread_key"] == NEW_KEY
    assert channel["address"] == {"channel_id": "channel-1", **STARTED["address"]}
    assert channel["delivery_id"] == action_id


def test_a_paused_schedule_opens_its_thread_with_a_notice_and_reports_the_pause_there(
    sent: SimpleNamespace,
) -> None:
    sent.started = STARTED
    _agent().invoke(_user("approve?"), _config(CRON_USER, metadata=_new_thread_cron()))

    ((action),) = sent.actions
    assert action["action_id"] == str(uuid.uuid5(uuid.NAMESPACE_URL, "mda-paused:run-1"))
    assert (action["address"], action["follow_thread"], action["message"]) == (
        {"conversation_id": "C1"},
        True,
        {"text": delivery.PAUSED_NOTICE},
    )
    sent.hand_off.assert_awaited_once()
    ((body),) = sent.events
    assert (body["type"], body["source_thread_key"], body["address"], body["run_thread_id"]) == (
        "run.interrupted",
        NEW_KEY,
        STARTED["address"],
        "t1",
    )


def test_a_deliver_to_conversation_opens_through_the_deployments_channel(
    sent: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGSMITH_HOST_PROJECT_ID", "deployment-1")
    user = {key: value for key, value in CRON_USER.items() if key != "mda_source_thread_id"}
    deliver_to = {
        "channel": "slack",
        "to": {"type": "provider_conversation", "conversationId": "C1"},
    }
    _agent().invoke(
        _user("hi"),
        _config(user, metadata={"mda_managed": True}),
        context={"mda_schedule": {"name": "daily", "deliver_to": deliver_to, "new_thread": True}},
    )

    ((deployment, name, action),) = [call.args for call in sent.deployment_action.await_args_list]
    assert (deployment, name) == ("deployment-1", "slack")
    assert (action["address"], action["follow_thread"], action["message"]) == (
        {"conversation_id": "C1"},
        True,
        {"text": "re: hi"},
    )
    assert sent.hand_off.await_args.args[0]["conversation"] == {
        "provider": "slack",
        "source_thread_key": NEW_KEY,
    }


def test_a_fixed_thread_deliver_to_schedule_keeps_its_thread(
    sent: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGSMITH_HOST_PROJECT_ID", "deployment-1")
    deliver = AsyncMock(return_value="posted")
    monkeypatch.setattr(delivery, "deliver_schedule_result", deliver)
    user = {key: value for key, value in CRON_USER.items() if key != "mda_source_thread_id"}
    deliver_to = {
        "channel": "slack",
        "to": {"type": "provider_conversation", "conversationId": "C1"},
    }
    _agent().invoke(
        _user("hi"),
        _config(user, metadata={"mda_managed": True}),
        context={"mda_schedule": {"name": "daily", "deliver_to": deliver_to}},
    )

    deliver.assert_awaited_once()
    sent.deployment_action.assert_not_awaited()
    sent.hand_off.assert_not_awaited()


@pytest.mark.parametrize(
    ("metadata", "outcome", "posted"),
    [
        ({"mda_managed": True}, "posted", True),
        ({}, "posted", False),
        ({"mda_managed": True}, "failed", True),
    ],
    ids=["trusted", "forged", "failed"],
)
def test_a_deliver_to_thread_posts_a_trusted_schedules_answer(
    sent: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    metadata: dict[str, object],
    outcome: str,
    posted: bool,
) -> None:
    deliver = AsyncMock(return_value=outcome)
    monkeypatch.setattr(delivery, "deliver_schedule_result", deliver)
    user = {key: value for key, value in CRON_USER.items() if key != "mda_source_thread_id"}
    to = {"type": "provider_thread", "conversation_id": "C1", "thread_id": "171.1"}

    def run() -> None:
        _agent().invoke(
            _user("hi"),
            _config(user, metadata=metadata),
            context={
                "mda_schedule": {"name": "daily", "deliver_to": {"channel": "slack", "to": to}}
            },
        )

    if outcome == "failed":
        with pytest.raises(RuntimeError, match="Scheduled channel delivery failed"):
            run()
    else:
        run()
    if posted:
        deliver.assert_awaited_once_with(
            deliver_to={"channel": "slack", "to": to, "auto_post": True}, reply_text="re: hi"
        )
    else:
        deliver.assert_not_awaited()
    sent.hand_off.assert_not_awaited()


def test_a_schedule_keeps_its_post_when_trigger_cannot_name_the_thread_it_started(
    sent: SimpleNamespace,
) -> None:
    _agent().invoke(_user("hi"), _config(CRON_USER, metadata=_new_thread_cron()))

    assert _texts(sent) == ["re: hi"]
    sent.hand_off.assert_not_awaited()


def test_a_stateless_schedule_posts_the_answer_its_agent_ended_with(
    sent: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliver = AsyncMock(return_value="posted")
    monkeypatch.setattr(delivery, "deliver_schedule_result", deliver)
    user = {key: value for key, value in CRON_USER.items() if key != "mda_source_thread_id"}
    to = {"type": "provider_conversation", "conversation_id": "C1"}
    _stateless_agent().invoke(
        _user("hi"),
        _config(user, metadata={"mda_managed": True}),
        context={"mda_schedule": {"name": "daily", "deliver_to": {"channel": "slack", "to": to}}},
    )

    deliver.assert_awaited_once_with(
        deliver_to={"channel": "slack", "to": to, "auto_post": True}, reply_text="re: hi"
    )
    sent.hand_off.assert_not_awaited()


def test_a_stateless_schedule_opens_no_thread(
    sent: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGSMITH_HOST_PROJECT_ID", "deployment-1")
    deliver = AsyncMock(return_value="posted")
    monkeypatch.setattr(delivery, "deliver_schedule_result", deliver)
    user = {key: value for key, value in CRON_USER.items() if key != "mda_source_thread_id"}
    to = {"type": "provider_conversation", "conversation_id": "C1"}
    _stateless_agent().invoke(
        _user("hi"),
        _config(user, metadata={"mda_managed": True}),
        context={
            "mda_schedule": {
                "name": "daily",
                "deliver_to": {"channel": "slack", "to": to},
                "new_thread": True,
            }
        },
    )

    deliver.assert_awaited_once()
    sent.deployment_action.assert_not_awaited()
    sent.hand_off.assert_not_awaited()


def _fixed_thread_cron() -> dict[str, object]:
    return {**_record(), "mda_managed": True, "mda_schedule_spec": {"new_thread": False}}


@pytest.mark.parametrize("use_async", [False, True])
def test_a_failed_schedule_reports_run_failed_in_its_trigger_thread(
    sent: SimpleNamespace, use_async: bool
) -> None:
    agent, config = _agent(), _config(CRON_USER, metadata=_fixed_thread_cron())
    run = (
        (lambda: asyncio.run(agent.ainvoke(_user("boom"), config)))
        if use_async
        else (lambda: agent.invoke(_user("boom"), config))
    )
    with pytest.raises(RuntimeError, match="secret detail"):
        run()

    assert sent.actions == []
    ((body),) = sent.events
    assert (body["type"], body["run_thread_id"], body["run_id"], body["source_thread_key"]) == (
        "run.failed",
        "t1",
        "run-1",
        KEY,
    )
    # Only a known-safe message leaves the run; Trigger shows its own error reply.
    assert "message" not in body


def test_a_schedule_that_misses_a_connection_says_which(sent: SimpleNamespace) -> None:
    with pytest.raises(Exception, match="clickup"):
        _agent().invoke(_user("missing"), _config(CRON_USER, metadata=_fixed_thread_cron()))

    ((body),) = sent.events
    assert body["type"] == "run.failed"
    assert "`clickup` connection isn't configured" in body["message"]["text"]


def test_a_failed_new_thread_schedule_reports_run_failed_in_the_thread_it_opens(
    sent: SimpleNamespace,
) -> None:
    sent.started = STARTED
    with pytest.raises(RuntimeError):
        _agent().invoke(_user("missing"), _config(CRON_USER, metadata=_new_thread_cron()))

    ((action),) = sent.actions
    assert action["action_id"] == delivery.failed_action_id("run-1")
    assert action["message"] == {"text": delivery.FAILED_THREAD_NOTICE}
    sent.hand_off.assert_awaited_once()
    ((body),) = sent.events
    assert (body["type"], body["source_thread_key"], body["address"], body["run_thread_id"]) == (
        "run.failed",
        NEW_KEY,
        STARTED["address"],
        "t1",
    )
    assert "`clickup` connection isn't configured" in body["message"]["text"]


def test_a_failed_deliver_to_schedule_posts_a_notice(
    sent: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliver = AsyncMock(return_value="posted")
    monkeypatch.setattr(delivery, "deliver_schedule_result", deliver)
    user = {key: value for key, value in CRON_USER.items() if key != "mda_source_thread_id"}
    to = {"type": "provider_thread", "conversation_id": "C1", "thread_id": "171.1"}
    with pytest.raises(RuntimeError):
        _agent().invoke(
            _user("boom"),
            _config(user, metadata={"mda_managed": True}),
            context={
                "mda_schedule": {"name": "daily", "deliver_to": {"channel": "slack", "to": to}}
            },
        )

    deliver.assert_awaited_once_with(
        deliver_to={"channel": "slack", "to": to, "auto_post": True},
        reply_text=delivery.FAILED_NOTICE,
    )


def test_a_failed_channel_message_run_is_left_to_trigger_ingress(sent: SimpleNamespace) -> None:
    with pytest.raises(RuntimeError):
        _agent().invoke(_user("boom"), _config(SLACK), context={"mda_channel": TRIGGER})

    assert (sent.actions, sent.events) == ([], [])


def test_a_cancelled_schedule_reports_nothing(sent: SimpleNamespace) -> None:
    started = asyncio.Event()

    async def wait(state: MessagesState) -> dict[str, object]:
        started.set()
        await asyncio.sleep(60)
        return {}

    graph = (
        StateGraph(MessagesState)
        .add_node("wait", wait)
        .add_edge(START, "wait")
        .add_edge("wait", END)
        .compile(checkpointer=InMemorySaver())
    )
    graph.__class__ = ManagedCompiledAgent

    async def cancel() -> None:
        run = asyncio.create_task(
            graph.ainvoke(_user("hi"), _config(CRON_USER, metadata=_fixed_thread_cron()))
        )
        await started.wait()
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

    asyncio.run(cancel())

    assert (sent.actions, sent.events) == ([], [])
