"""MDA's Slack channel decides what each message starts and builds the turn's prompt."""

import asyncio
import uuid
from collections.abc import Mapping
from typing import Any, cast

import pytest
import rt.slack as slack
from langgraph_sdk.schema import ThreadState
from managed_deepagents import ChannelReplyResult, ChannelRun, SlackChannelThread, SlackMessageEvent
from managed_deepagents._channels.http.types import ChannelName
from managed_deepagents._channels.run_input import RunChannel, normalize_run_inputs
from pydantic import BaseModel
from runtime_api.endpoint import Endpoint
from runtime_api.slack import (
    SLACK_MESSAGE_FACTS,
    START_SLACK_TURN,
    SlackMessageFacts,
    SlackMessageFactsRequest,
    SlackTurnDeclined,
    SlackTurnPrompt,
    SlackTurnRun,
    SlackTurnStarted,
    StartSlackTurn,
)

_THREAD_ID = str(uuid.uuid4())
_RUN = SlackTurnRun(
    input={},
    context={"__run_token": "signed"},
    metadata={"channel": "slack"},
    webhook="https://backend.example/webhooks/run-complete",
)
_PROMPT = SlackTurnPrompt(
    first_turn=False,
    sender_name="Ada",
    logins={"U1": "ada"},
    person_ids={"U1": "user:1"},
    channel={"id": "slack:C1", "platform": "slack", "name": "#eng", "thread_id": "171.1"},
)
_HISTORY = [
    {"ts": "171.1", "user": "U2", "text": "the build is red"},
    {"ts": "171.2", "user": "U1", "text": "<@UBOT> and the tests too"},
]


def _thread(values: Mapping[str, Any] | None = None) -> SlackChannelThread:
    """A conversation's thread whose state holds ``values``; ``None`` before any run."""

    async def get_state() -> ThreadState | None:
        if values is None:
            return None
        return cast(ThreadState, {"values": dict(values), "next": [], "tasks": []})

    return SlackChannelThread(
        id=_THREAD_ID,
        source_thread_key="slack:v1:A1:T1:C1:171.1",
        channel=ChannelName(name="slack"),
        _get_state=get_state,
    )


def _event(text: str = "<@UBOT> and the tests too", **changes: Any) -> SlackMessageEvent:
    fields: dict[str, Any] = {
        "key": "mention",
        "delivery_id": "delivery-1",
        "text": text,
        "user_id": "U1",
        "channel_id": "C1",
        "message_ts": "171.2",
        "thread_ts": "171.1",
        "payload": {"type": "message", "thread_ts": "171.1", "text": text},
        "team_id": "T1",
    }
    fields.update(changes)
    return SlackMessageEvent(**fields)


class _Backend:
    """The backend's two calls, as the handler makes them."""

    def __init__(self, facts: SlackMessageFacts, started: SlackTurnStarted) -> None:
        self.facts, self.started = facts, started
        self.asked_facts: list[SlackMessageFactsRequest] = []
        self.turns: list[StartSlackTurn] = []

    async def __call__(
        self, endpoint: Endpoint[BaseModel, BaseModel], request: BaseModel, *, msg_id: str
    ) -> BaseModel:
        if endpoint is SLACK_MESSAGE_FACTS:
            assert msg_id == "facts_delivery-1"
            self.asked_facts.append(cast(SlackMessageFactsRequest, request))
            return self.facts
        assert endpoint is START_SLACK_TURN
        assert msg_id == "msg_delivery-1"
        self.turns.append(cast(StartSlackTurn, request))
        return self.started


@pytest.fixture
def slack_reads(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[object]]:
    """Slack as the bot token reads it: the thread, names, no links and no images."""
    reads: dict[str, list[object]] = {"history": [], "images": []}
    monkeypatch.setattr(slack, "_bot", ("UBOT", "openswe"))

    async def history(channel_id: str, thread_ts: str) -> list[dict[str, Any]]:
        reads["history"].append((channel_id, thread_ts))
        return [dict(message) for message in _HISTORY]

    async def names(user_ids: object) -> dict[str, str]:
        return {"U1": "Ada", "U2": "Grace"}

    async def channel_names(channel_ids: object) -> dict[str, str]:
        return {}

    async def links(messages: object, names: object) -> tuple[str, list[str]]:
        return "", []

    async def images(urls: object) -> list[object]:
        reads["images"].append(list(cast(list[str], urls)))
        return []

    monkeypatch.setattr(slack.slack_prompt, "slack_history", history)
    monkeypatch.setattr(slack.slack_prompt, "user_names", names)
    monkeypatch.setattr(slack.slack_prompt, "public_channel_names", channel_names)
    monkeypatch.setattr(slack.slack_prompt, "resolved_links", links)
    monkeypatch.setattr(slack.slack_prompt, "image_blocks", images)
    return reads


def _backend(
    monkeypatch: pytest.MonkeyPatch,
    started: SlackTurnStarted,
    facts: SlackMessageFacts | None = None,
) -> _Backend:
    backend = _Backend(facts or SlackMessageFacts(conversation="mda"), started)
    monkeypatch.setattr(slack, "ask_backend", backend)
    return backend


def _texts(run_input: Mapping[str, Any]) -> str:
    return "\n".join(str(message["content"]) for message in run_input["messages"])


@pytest.mark.parametrize(("interrupts", "strategy"), [(True, "interrupt"), (False, "enqueue")])
@pytest.mark.usefixtures("slack_reads")
def test_a_turn_redirects_the_work_in_progress_or_waits_as_the_backend_says(
    monkeypatch: pytest.MonkeyPatch, interrupts: bool, strategy: str
) -> None:
    backend = _backend(
        monkeypatch, SlackTurnStarted(run=_RUN, interrupts=interrupts, prompt=_PROMPT)
    )

    result = asyncio.run(slack.start_turn(_event(), _thread()))

    [facts] = backend.asked_facts
    assert (facts.app_id, facts.team_id, facts.channel_id) == ("A1", "T1", "C1")
    assert facts.explicit_mention
    [turn] = backend.turns
    assert turn.kind == "turn"
    # What the handler read from Slack, for what the backend needs it.
    assert turn.clean_text == "@openswe and the tests too"
    assert turn.context_user_ids == ["U2", "U1"]
    assert turn.participant_user_ids == ["U1", "U2"]
    assert not turn.has_images
    [run] = cast(list[ChannelRun], result)
    assert run["context"] == _RUN.context
    assert run["metadata"] == _RUN.metadata
    assert run["webhook"] == _RUN.webhook
    assert run["multitask_strategy"] == strategy
    # The prompt the handler built: the thread before the message, then the request.
    prompt = _texts(cast(Mapping[str, Any], run["input"]))
    assert "the build is red" in prompt
    assert "@openswe and the tests too" in prompt
    # A list of one run, as a dict alone would be state: MDA starts it on the conversation.
    [normalized] = normalize_run_inputs(
        result, RunChannel(name="slack", provider="slack", conversation_thread_id=_THREAD_ID)
    )
    assert "thread" not in normalized


@pytest.mark.usefixtures("slack_reads")
def test_a_turn_leaves_out_what_the_thread_already_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    _backend(monkeypatch, SlackTurnStarted(run=_RUN, prompt=_PROMPT))
    earlier = cast(list[ChannelRun], asyncio.run(slack.start_turn(_event(), _thread())))
    held = {"messages": cast(Mapping[str, Any], earlier[0]["input"])["messages"]}

    again = cast(list[ChannelRun], asyncio.run(slack.start_turn(_event(), _thread(held))))

    prompt = _texts(cast(Mapping[str, Any], again[0]["input"]))
    assert "the build is red" not in prompt
    assert "@openswe and the tests too" in prompt


def test_a_private_threads_prompt_quotes_and_shows_only_its_people(
    monkeypatch: pytest.MonkeyPatch, slack_reads: dict[str, list[object]]
) -> None:
    async def history(channel_id: str, thread_ts: str) -> list[dict[str, Any]]:
        return [
            {"ts": "171.1", "user": "U2", "text": "secret https://example.com/a.png"},
            {"ts": "171.2", "user": "U1", "text": "<@UBOT> and the tests too"},
        ]

    monkeypatch.setattr(slack.slack_prompt, "slack_history", history)
    prompt = _PROMPT.model_copy(update={"promptable_user_ids": ["U1"]})
    _backend(monkeypatch, SlackTurnStarted(run=_RUN, prompt=prompt))

    [run] = cast(list[ChannelRun], asyncio.run(slack.start_turn(_event(), _thread())))

    assert "secret" not in _texts(cast(Mapping[str, Any], run["input"]))
    assert slack_reads["images"] == [[]]


def test_a_message_the_backend_starts_nothing_for_starts_nothing(
    monkeypatch: pytest.MonkeyPatch, slack_reads: dict[str, list[object]]
) -> None:
    _backend(monkeypatch, SlackTurnStarted())

    assert asyncio.run(slack.start_turn(_event(), _thread())) is None


@pytest.mark.parametrize(
    ("declined", "told"),
    [
        (
            SlackTurnDeclined(reason="sign_in", settings_url="https://openswe.test/settings"),
            "👋 I couldn't resolve your GitHub account from Slack. Sign in with GitHub and "
            "connect your Slack account in <https://openswe.test/settings|your Open SWE "
            "settings>, then tag me again.",
        ),
        (
            SlackTurnDeclined(reason="sign_in_again", settings_url="https://openswe.test/settings"),
            "🔐 Your GitHub sign-in is no longer valid, so I can't resolve your GitHub "
            "account. Re-connect it in <https://openswe.test/settings|your Open SWE "
            "settings>, then tag me again.",
        ),
        (
            SlackTurnDeclined(reason="sign_in"),
            "👋 I couldn't resolve your GitHub account from Slack. Sign in with GitHub and "
            "connect your Slack account in your Open SWE settings, then tag me again.",
        ),
        (
            SlackTurnDeclined(
                reason="breakout",
                message="I can't post in <#C2>. Add me to that channel, then try again.",
            ),
            "I can't post in <#C2>. Add me to that channel, then try again.",
        ),
    ],
    ids=["sign-in", "sign-in-again", "no-dashboard-url", "breakout"],
)
@pytest.mark.usefixtures("slack_reads")
def test_a_message_the_backend_declines_gets_a_reply_and_no_run(
    monkeypatch: pytest.MonkeyPatch, declined: SlackTurnDeclined, told: str
) -> None:
    _backend(monkeypatch, SlackTurnStarted(declined=declined))

    result = asyncio.run(slack.start_turn(_event(), _thread()))

    assert isinstance(result, ChannelReplyResult)
    assert result.message == {"type": "content", "content": told}


@pytest.mark.usefixtures("slack_reads")
def test_a_mention_in_a_channel_with_external_participants_hears_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _backend(monkeypatch, SlackTurnStarted(), SlackMessageFacts(channel="external"))

    result = asyncio.run(slack.start_turn(_event(), _thread()))

    assert isinstance(result, ChannelReplyResult)
    assert result.message == {"type": "content", "content": slack.EXTERNAL_CHANNEL}
    assert backend.turns == []


@pytest.mark.usefixtures("slack_reads")
def test_a_concierge_dms_turn_runs_on_the_dms_one_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    dm_thread = uuid.uuid4()
    backend = _backend(
        monkeypatch,
        SlackTurnStarted(run=_RUN, interrupts=True, thread_id=dm_thread, prompt=_PROMPT),
        SlackMessageFacts(dm=True, concierge=True, conversation="mda"),
    )
    event = _event(
        "and the tests too", key="directMessage", payload={"type": "message", "channel_type": "im"}
    )

    result = asyncio.run(slack.start_turn(event, _thread()))

    [turn] = backend.turns
    assert turn.concierge and turn.treat_all_messages_as_mentions
    [run] = cast(list[ChannelRun], result)
    # The agent answers in Slack itself, so the run posts nothing.
    assert run.get("thread") == {"mode": "persistent", "id": str(dm_thread)}
    assert "auto_post" not in run
    assert run.get("multitask_strategy") == "interrupt"


@pytest.mark.parametrize(
    ("text", "facts", "kind", "move_channel_id"),
    [
        (
            "<@UBOT> /btw why is it red?",
            SlackMessageFacts(conversation="backend"),
            "by_the_way",
            "",
        ),
        ("<@UBOT> /breakout fix the tests", SlackMessageFacts(conversation="mda"), "breakout", ""),
        ("<@UBOT> /breakout:web explain it", SlackMessageFacts(), "web", ""),
        ("<@UBOT> /breakout <#C2|ops>", SlackMessageFacts(conversation="mda"), "move", "C2"),
    ],
    ids=["btw", "breakout", "web", "move"],
)
def test_commands_come_back_built_by_the_backend(
    monkeypatch: pytest.MonkeyPatch,
    slack_reads: dict[str, list[object]],
    text: str,
    facts: SlackMessageFacts,
    kind: str,
    move_channel_id: str,
) -> None:
    built = _RUN.model_copy(update={"input": {"messages": [{"role": "human", "content": "x"}]}})
    backend = _backend(monkeypatch, SlackTurnStarted(run=built), facts)

    [run] = cast(list[ChannelRun], asyncio.run(slack.start_turn(_event(text), _thread())))

    [turn] = backend.turns
    assert (turn.kind, turn.move_channel_id) == (kind, move_channel_id)
    assert run["input"] == built.input
    # The backend builds these whole: the handler reads nothing from Slack.
    assert slack_reads["history"] == []


@pytest.mark.usefixtures("slack_reads")
def test_a_branch_starts_with_what_the_conversations_thread_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = uuid.uuid4()
    built = _RUN.model_copy(
        update={"input": {"messages": [{"role": "human", "content": "why is it red?"}]}}
    )
    _backend(
        monkeypatch,
        SlackTurnStarted(run=built, thread_id=branch, branches_conversation=True),
    )
    held = [
        {"type": "human", "content": "the build is red", "id": "m1"},
        {"type": "ai", "content": "Looking.", "id": "m2"},
    ]

    [run] = cast(
        list[ChannelRun],
        asyncio.run(
            slack.start_turn(
                _event("<@UBOT> /btw why is it red?"),
                _thread({"messages": held, "todos": ["fix CI"]}),
            )
        ),
    )

    # The new thread starts with the conversation's history and state, then the question.
    assert run.get("thread") == {"mode": "persistent", "id": str(branch)}
    run_input = cast(dict[str, Any], run["input"])
    assert run_input["messages"] == [*held, *cast(list[Any], built.input["messages"])]
    assert run_input["todos"] == ["fix CI"]


@pytest.mark.parametrize("values", [None, {}], ids=["no-run-yet", "empty"])
@pytest.mark.usefixtures("slack_reads")
def test_a_branch_of_a_thread_that_holds_nothing_starts_with_the_question(
    monkeypatch: pytest.MonkeyPatch, values: dict[str, Any] | None
) -> None:
    built = _RUN.model_copy(update={"input": {"messages": [{"role": "human", "content": "x"}]}})
    _backend(
        monkeypatch,
        SlackTurnStarted(run=built, thread_id=uuid.uuid4(), branches_conversation=True),
    )

    [run] = cast(
        list[ChannelRun],
        asyncio.run(slack.start_turn(_event("<@UBOT> /btw why?"), _thread(values))),
    )

    assert run["input"] == built.input


@pytest.mark.usefixtures("slack_reads")
def test_a_branch_whose_conversation_cant_be_read_is_still_answered(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    built = _RUN.model_copy(update={"input": {"messages": [{"role": "human", "content": "x"}]}})
    _backend(
        monkeypatch,
        SlackTurnStarted(run=built, thread_id=uuid.uuid4(), branches_conversation=True),
    )

    async def unreadable() -> ThreadState | None:
        raise RuntimeError("agent server is down")

    thread = _thread()
    object.__setattr__(thread, "_get_state", unreadable)

    with caplog.at_level("WARNING"):
        [run] = cast(
            list[ChannelRun],
            asyncio.run(slack.start_turn(_event("<@UBOT> /btw why?"), thread)),
        )

    assert run["input"] == built.input
    assert "Could not read the conversation a Slack branch starts with" in caplog.text


@pytest.mark.parametrize(
    ("event", "facts"),
    [
        # Addressed to another person.
        (_event("<@U9> can you look?", key="message"), SlackMessageFacts(conversation="mda")),
        # An untagged reply nobody asked the agent for.
        (_event("thanks!", key="message"), SlackMessageFacts(conversation="mda")),
        # A conversation the backend holds.
        (_event(), SlackMessageFacts(conversation="backend")),
        # A code channel's one session stays with the backend.
        (_event(), SlackMessageFacts(code_channel=True, conversation="mda")),
        # A bot that isn't on the allow-list.
        (
            _event(user_id=None, payload={"type": "message", "bot_id": "B1", "app_id": "A9"}),
            SlackMessageFacts(conversation="mda"),
        ),
        # A channel Slack can't confirm has no external participants.
        (_event(), SlackMessageFacts(channel="unverified")),
    ],
    ids=["other-person", "untagged", "backend", "code-channel", "unlisted-bot", "unverified"],
)
@pytest.mark.usefixtures("slack_reads")
def test_messages_not_for_the_agent_start_nothing(
    monkeypatch: pytest.MonkeyPatch, event: SlackMessageEvent, facts: SlackMessageFacts
) -> None:
    backend = _backend(monkeypatch, SlackTurnStarted(run=_RUN, prompt=_PROMPT), facts)

    assert asyncio.run(slack.start_turn(event, _thread())) is None
    assert backend.turns == []


@pytest.mark.usefixtures("slack_reads")
def test_an_untagged_kitchen_channel_message_or_solo_followup_is_a_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for facts in (
        SlackMessageFacts(kitchen_channel=True, conversation="new"),
        SlackMessageFacts(solo_followup=True, conversation="mda"),
    ):
        backend = _backend(monkeypatch, SlackTurnStarted(run=_RUN, prompt=_PROMPT), facts)

        assert asyncio.run(slack.start_turn(_event("and the tests", key="message"), _thread()))
        [turn] = backend.turns
        assert turn.treat_all_messages_as_mentions
        assert turn.kitchen_channel is facts.kitchen_channel


def test_the_channel_asks_its_handlers_for_every_message_and_click() -> None:
    from channels.slack import channel  # noqa: PLC0415
    from rt.slack_clicks import start_click  # noqa: PLC0415

    handlers = channel.on_event or {}
    assert channel.multitask_strategy == "interrupt"
    # The agent answers with Open SWE's Slack tools: the channel posts nothing itself.
    assert channel.auto_post is False
    assert set(handlers) == {
        "mention",
        "directMessage",
        "message",
        "interaction",
        "shortcut",
        "channel",
    }
    assert all(handlers[key] is slack.start_turn for key in ("mention", "directMessage", "message"))
    assert handlers["interaction"] is start_click
