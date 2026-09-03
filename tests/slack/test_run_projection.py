from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent import server
from agent.middleware import slack_transcript as middleware_module
from agent.middleware.slack_transcript import SlackTranscriptMiddleware
from agent.surfaces import projector

CHANNEL_LOCATION = {
    "channel_id": "C-code",
    "thread_ts": "0",
    "surface": "slack_channel",
    "team_id": "T1",
    "triggering_user_id": "U1",
    "triggering_event_ts": "1717171717.000100",
}
THREAD_LOCATION = {"channel_id": "C1", "thread_ts": "1717171717.000100"}


class FakeStore:
    """The durable half of a transcript: what survives a pod dying mid-run."""

    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def aget(self, namespace: tuple[str, ...], key: str) -> Any:
        value = self.items.get((namespace, key))
        return type("Item", (), {"value": value})() if value is not None else None

    async def aput(self, namespace: tuple[str, ...], key: str, value: dict[str, Any]) -> None:
        self.items[(namespace, key)] = dict(value)


class FakeTranscript:
    """Stands in for the Slack side, recording what would have been streamed."""

    started = 0

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.run_id = kwargs["run_id"]
        self.channel_id = kwargs["channel_id"]
        self.message_ts: str | None = None
        self.streamed_chars = 0
        self.pending: list[dict[str, Any]] = []
        self.said: list[str] = []
        self.plan_title = ""
        self.cards: list[tuple[str, str]] = []
        self.stopped: str | None = None
        FakeTranscript.instances.append(self)

    instances: list["FakeTranscript"] = []

    async def start(self) -> bool:
        FakeTranscript.started += 1
        self.message_ts = f"stream-{FakeTranscript.started}"
        return True

    def say(self, text: str) -> None:
        self.said.append(text)

    def name_plan(self, title: str) -> None:
        self.plan_title = self.plan_title or title

    def tool_started(self, call_id: str, name: str, _args: Any) -> None:
        self.cards.append((call_id, "started"))

    def tool_finished(self, call_id: str, *, failed: bool = False) -> None:
        self.cards.append((call_id, "failed" if failed else "finished"))

    def restore_pending(self, chunks: list[dict[str, Any]]) -> None:
        self.pending = list(chunks)

    async def flush(self, *, force: bool = False) -> None:
        self.streamed_chars = sum(len(text) for text in self.said)
        self.pending = []

    async def stop(self, status: str) -> None:
        self.stopped = status


@pytest.fixture
def slack(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    store = FakeStore()
    FakeTranscript.instances = []
    FakeTranscript.started = 0
    monkeypatch.setattr(middleware_module, "SlackTranscript", FakeTranscript)
    monkeypatch.setattr(middleware_module, "langgraph_client", lambda: AsyncMock())
    monkeypatch.setattr(middleware_module, "get_store", lambda: store)
    return store


def _middleware(run_key: str = "run-key-1") -> SlackTranscriptMiddleware:
    return SlackTranscriptMiddleware(
        thread_id="thread-1",
        run_key=run_key,
        channel_id="C-code",
        reply_thread_ts="",
        session_thread_ts="0",
        triggering_user_id="U1",
        triggering_event_ts="1717171717.000100",
        team_id="T1",
    )


def _state(*messages: Any) -> dict[str, Any]:
    return {"messages": list(messages)}


async def test_the_agents_words_go_out_without_a_posting_tool(slack: FakeStore) -> None:
    transcript = _middleware()
    state = _state(HumanMessage(content="fix the login test", id="h1"))
    await transcript.abefore_agent(state, None)  # type: ignore[arg-type]

    answered = _state(*state["messages"], AIMessage(content="On it — reading the test.", id="a1"))
    await transcript.abefore_model(answered, None)  # type: ignore[arg-type]

    assert FakeTranscript.instances[0].said == ["On it — reading the test."]


async def test_what_the_user_already_said_is_not_read_back_to_them(slack: FakeStore) -> None:
    """State holds the whole conversation; only this turn is new."""
    transcript = _middleware()
    history = _state(
        HumanMessage(content="earlier question", id="h1"),
        AIMessage(content="earlier answer", id="a1"),
        HumanMessage(content="new question", id="h2"),
    )
    await transcript.abefore_agent(history, None)  # type: ignore[arg-type]

    answered = _state(*history["messages"], AIMessage(content="new answer", id="a2"))
    await transcript.abefore_model(answered, None)  # type: ignore[arg-type]

    assert FakeTranscript.instances[0].said == ["new answer"]


async def test_a_resumed_run_keeps_writing_into_the_same_message(slack: FakeStore) -> None:
    """A pod died mid-run: the platform re-queues it, the transcript picks up."""
    first = _middleware()
    state = _state(HumanMessage(content="go", id="h1"))
    await first.abefore_agent(state, None)  # type: ignore[arg-type]
    said = _state(*state["messages"], AIMessage(content="starting", id="a1"))
    await first.abefore_model(said, None)  # type: ignore[arg-type]

    # Same run, new process: nothing in memory, everything from the store.
    resumed = _middleware()
    await resumed.abefore_agent(said, None)  # type: ignore[arg-type]
    continued = _state(*said["messages"], AIMessage(content="continuing", id="a2"))
    await resumed.abefore_model(continued, None)  # type: ignore[arg-type]

    assert FakeTranscript.started == 1, "resuming must not open a second Slack message"
    assert FakeTranscript.instances[-1].message_ts == "stream-1"
    assert FakeTranscript.instances[-1].said == ["continuing"], "already-sent words are not resent"


async def test_a_word_already_delivered_is_never_delivered_twice(slack: FakeStore) -> None:
    transcript = _middleware()
    state = _state(HumanMessage(content="go", id="h1"))
    await transcript.abefore_agent(state, None)  # type: ignore[arg-type]
    said = _state(*state["messages"], AIMessage(content="only once", id="a1"))

    await transcript.abefore_model(said, None)  # type: ignore[arg-type]
    await transcript.abefore_model(said, None)  # type: ignore[arg-type]
    await transcript.aafter_agent(said, None)  # type: ignore[arg-type]

    assert FakeTranscript.instances[0].said == ["only once"]


async def test_a_tool_call_shows_as_a_card_around_the_call(slack: FakeStore) -> None:
    transcript = _middleware()
    await transcript.abefore_agent(_state(), None)  # type: ignore[arg-type]
    request = type(
        "Request", (), {"tool_call": {"id": "call-1", "name": "read_file", "args": {}}}
    )()

    async def handler(_request: Any) -> ToolMessage:
        return ToolMessage(content="ok", tool_call_id="call-1")

    await transcript.awrap_tool_call(request, handler)  # type: ignore[arg-type]

    assert FakeTranscript.instances[0].cards == [("call-1", "started"), ("call-1", "finished")]


async def test_a_tool_that_raises_is_marked_failed_and_the_error_still_raises(
    slack: FakeStore,
) -> None:
    transcript = _middleware()
    await transcript.abefore_agent(_state(), None)  # type: ignore[arg-type]
    request = type("Request", (), {"tool_call": {"id": "call-1", "name": "execute", "args": {}}})()

    async def handler(_request: Any) -> ToolMessage:
        raise RuntimeError("sandbox gone")

    with pytest.raises(RuntimeError, match="sandbox gone"):
        await transcript.awrap_tool_call(request, handler)  # type: ignore[arg-type]

    assert FakeTranscript.instances[0].cards == [("call-1", "started"), ("call-1", "failed")]


async def test_the_turn_is_closed_when_the_agent_stops(slack: FakeStore) -> None:
    transcript = _middleware()
    state = _state(HumanMessage(content="go", id="h1"), AIMessage(content="done", id="a1"))
    await transcript.abefore_agent(_state(state["messages"][0]), None)  # type: ignore[arg-type]

    await transcript.aafter_agent(state, None)  # type: ignore[arg-type]

    assert FakeTranscript.instances[0].said == ["done"]
    assert FakeTranscript.instances[0].stopped == "success"
    assert slack.items[(("slack_transcript", "thread-1"), "run-key-1")]["done"] is True


async def test_the_next_turn_opens_its_own_message(slack: FakeStore) -> None:
    """A repeating cron reuses one run key, so a closed turn must not be resumed."""
    first = _middleware()
    state = _state(HumanMessage(content="go", id="h1"), AIMessage(content="first", id="a1"))
    await first.abefore_agent(_state(state["messages"][0]), None)  # type: ignore[arg-type]
    await first.aafter_agent(state, None)  # type: ignore[arg-type]

    second = _middleware()
    await second.abefore_agent(state, None)  # type: ignore[arg-type]
    next_turn = _state(*state["messages"], AIMessage(content="second", id="a2"))
    await second.abefore_model(next_turn, None)  # type: ignore[arg-type]

    assert FakeTranscript.started == 2
    assert FakeTranscript.instances[-1].said == ["second"]


async def test_a_channel_that_will_not_stream_does_not_break_the_run(
    monkeypatch: pytest.MonkeyPatch, slack: FakeStore
) -> None:
    async def refuse(self: Any) -> bool:
        return False

    monkeypatch.setattr(FakeTranscript, "start", refuse)
    transcript = _middleware()
    state = _state(HumanMessage(content="go", id="h1"), AIMessage(content="hello", id="a1"))

    await transcript.abefore_agent(_state(state["messages"][0]), None)  # type: ignore[arg-type]
    await transcript.abefore_model(state, None)  # type: ignore[arg-type]
    await transcript.aafter_agent(state, None)  # type: ignore[arg-type]

    assert FakeTranscript.instances[0].said == []


async def test_no_store_means_no_transcript_rather_than_a_failed_run(
    monkeypatch: pytest.MonkeyPatch, slack: FakeStore
) -> None:
    def no_store() -> Any:
        raise RuntimeError("no store in this runtime")

    monkeypatch.setattr(middleware_module, "get_store", no_store)
    transcript = _middleware()
    state = _state(HumanMessage(content="go", id="h1"), AIMessage(content="hello", id="a1"))

    await transcript.abefore_agent(state, None)  # type: ignore[arg-type]
    await transcript.abefore_model(state, None)  # type: ignore[arg-type]

    # Without a record there is no baseline, so the turn's text still goes out once.
    assert FakeTranscript.instances[0].said == ["hello"]


@pytest.mark.parametrize(
    ("configurable", "expected"),
    [
        ({"slack_thread": CHANNEL_LOCATION, "prepare_run_id": "p1"}, 1),
        ({"slack_thread": THREAD_LOCATION, "prepare_run_id": "p1"}, 0),
        ({"prepare_run_id": "p1"}, 0),
        ({"slack_thread": CHANNEL_LOCATION}, 0),
        ({}, 0),
    ],
    ids=["code channel", "slack thread", "no location", "no run key", "nothing"],
)
def test_only_a_channel_session_gets_a_transcript(
    configurable: dict[str, Any], expected: int
) -> None:
    assert len(server._transcript_middleware(configurable, "thread-1")) == expected


def test_the_transcript_is_told_where_to_write() -> None:
    (transcript,) = server._transcript_middleware(
        {
            "slack_thread": {**CHANNEL_LOCATION, "reply_thread_ts": "1717171718.000200"},
            "prepare_run_id": "p1",
        },
        "thread-1",
    )

    assert isinstance(transcript, SlackTranscriptMiddleware)
    assert transcript.channel_id == "C-code"
    assert transcript.reply_thread_ts == "1717171718.000200"
    assert transcript.session_thread_ts == "0"
    assert transcript.run_key == "p1"
    assert transcript.team_id == "T1"


def test_the_projector_no_longer_watches_from_outside() -> None:
    """Delivery lives in the run; nothing external subscribes to it."""
    assert not hasattr(projector, "project_run_into_slack")
    assert not hasattr(projector, "start_projection")


async def test_a_tool_whose_effect_is_the_channel_gets_no_card(slack: FakeStore) -> None:
    """A card describing a Slack post would describe what the reader is looking at."""
    transcript = _middleware()
    await transcript.abefore_agent(_state(), None)  # type: ignore[arg-type]
    request = type(
        "Request",
        (),
        {
            "tool_call": {"id": "call-1", "name": "slack_reply_to_message", "args": {}},
            "state": _state(),
        },
    )()

    async def handler(_request: Any) -> ToolMessage:
        return ToolMessage(content="ok", tool_call_id="call-1")

    await transcript.awrap_tool_call(request, handler)  # type: ignore[arg-type]

    assert [t.cards for t in FakeTranscript.instances] in ([], [[]])


async def test_the_plan_block_is_named_from_the_turns_first_words(slack: FakeStore) -> None:
    """Steps collect under one named block instead of a card per call."""
    transcript = _middleware()
    state = _state(HumanMessage(content="go", id="h1"))
    await transcript.abefore_agent(state, None)  # type: ignore[arg-type]
    said = _state(
        *state["messages"],
        AIMessage(content="Reading the failing test first.", id="a1"),
        AIMessage(content="Now fixing it.", id="a2"),
    )

    await transcript.abefore_model(said, None)  # type: ignore[arg-type]

    assert FakeTranscript.instances[0].plan_title == "Reading the failing test first."
