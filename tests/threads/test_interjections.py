import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import NotRequired, TypedDict
from uuid import UUID, uuid4, uuid7

import pytest
from langchain_core.messages import AIMessage, HumanMessage, messages_from_dict, messages_to_dict
from pydantic import JsonValue, TypeAdapter
from runtime_api.loop import ModelResult, RunStarted
from sqlalchemy import text

from openswe import dispatch
from openswe.agent_api.run_calls import TokenRun
from openswe.agent_api.run_kinds import RunKind
from openswe.agent_api.tokens import RemoteRun
from openswe.database import postgres
from openswe.github.pull_requests import PullRequest
from openswe.human_review.lifecycle import ReviewCard
from openswe.human_review.requests import HumanReviewRequest
from openswe.input_messages import RunInput, RunMessage, Surface, build_input_messages
from openswe.model_routing import RoutingArm
from openswe.prompts import prompt
from openswe.threads import index, workflow_approval_api
from openswe.threads.index import STOPPED_AT_KEY
from openswe.threads.interjections import RunInterjections
from openswe.utils import langsmith as ls_utils
from openswe.utils.dashboard_handoff import dashboard_input_context

pytestmark = pytest.mark.usefixtures("registry_db")


class _Agent(RunKind):
    assistant_id = "agent"


class _Source(TypedDict):
    metadata: dict[str, JsonValue]


class _Row(TypedDict):
    """The parts of a feedback row the tests read."""

    id: str
    key: str
    score: float | None
    value: NotRequired[str]
    feedback_thread_id: str
    feedback_source: _Source


_ROW: TypeAdapter[_Row] = TypeAdapter(_Row)


class _Feedback:
    """LangSmith as the feedback writer reaches it: every row it is asked to write."""

    def __init__(self) -> None:
        self.rows: dict[str, _Row] = {}

    async def _arequest_with_retries(self, method: str, endpoint: str, **kwargs: object) -> None:
        if method == "POST":
            row = _ROW.validate_python(kwargs["json"])
            self.rows[row["id"]] = row

    def of(self, thread_id: str, key: str = "human_interjection") -> list[tuple[object, ...]]:
        return sorted(
            (key, row["score"], row.get("value"), row["feedback_source"]["metadata"]["surface"])
            for row in self.rows.values()
            if row["feedback_thread_id"] == thread_id and row["key"] == key
        )

    def sources(self, thread_id: str, key: str) -> list[dict[str, JsonValue]]:
        """What each ``key`` row on the thread says about the message it records."""
        return [
            row["feedback_source"]["metadata"]
            for row in self.rows.values()
            if row["feedback_thread_id"] == thread_id and row["key"] == key
        ]


@pytest.fixture
def feedback(monkeypatch: pytest.MonkeyPatch) -> _Feedback:
    written = _Feedback()
    monkeypatch.setattr(ls_utils, "_build_langsmith_client", lambda: written)

    async def project(_name: str) -> str:
        return "project-id"

    monkeypatch.setattr(ls_utils, "_resolve_project_id_by_name", project)
    return written


def _typed(
    text_: str, surface: Surface = "slack", *, relayed: bool = False
) -> dict[str, JsonValue]:
    context = (
        dashboard_input_context("github:octocat", "thread-0")
        if relayed
        else {"sender_id": "github:octocat", "surface": surface, "kind": "human"}
    )
    (message,) = build_input_messages(text_, context)
    return _human(message)


def _human(message: RunMessage) -> dict[str, JsonValue]:
    """``message`` as a run reports it once it reaches a model call."""
    content = message["content"]
    assert isinstance(content, str)
    human = HumanMessage(
        content=content, additional_kwargs=message.get("additional_kwargs", {}), id=str(uuid4())
    )
    return messages_to_dict([human])[0]


def _id(message: dict[str, JsonValue]) -> str | None:
    [human] = messages_from_dict([message])
    return human.id


def _run_id(requested: datetime) -> str:
    """An Agent Server run id: a UUIDv7 minted when the run was asked for."""
    millis = int(requested.timestamp() * 1000)
    return str(UUID(int=(millis << 80) | (uuid7().int & ((1 << 80) - 1))))


async def _thread(thread_id: str, **metadata: JsonValue) -> None:
    await index.create(thread_id, runtime="local", metadata=metadata)


async def _turn(
    thread_id: str,
    state: str,
    *,
    started_ago: timedelta,
    active_ago: timedelta | None = None,
    ended_ago: timedelta = timedelta(0),
) -> str:
    """A turn of the thread, which last recorded anything ``active_ago`` and, unless it
    is still running, ended ``ended_ago``; returns its run id."""
    now = datetime.now(UTC)
    run_id = str(uuid4())
    async with postgres.session() as session:
        await session.execute(
            text("INSERT INTO thread (thread_id) VALUES (:thread) ON CONFLICT DO NOTHING"),
            {"thread": thread_id},
        )
        await session.execute(
            text(
                "INSERT INTO thread_turn"
                " (turn_id, thread_id, run_id, state, requested_at, started_at, completed_at)"
                " VALUES (:turn, :thread, :run, :state, :at, :at, :ended)"
            ),
            {
                "turn": uuid4(),
                "thread": thread_id,
                "run": run_id,
                "state": state,
                "at": now - started_ago,
                "ended": None if state == "running" else now - ended_ago,
            },
        )
        if active_ago is not None:
            await session.execute(
                text(
                    "INSERT INTO thread_event"
                    " (thread_id, version, event_id, event_type, run_id, actor_kind, occurred_at,"
                    " payload) SELECT :thread, coalesce(max(version), 0) + 1, :event,"
                    " 'message.completed', :run, 'agent', :at, '{}' FROM thread_event"
                    " WHERE thread_id = :thread"
                ),
                {"thread": thread_id, "event": uuid4(), "run": run_id, "at": now - active_ago},
            )
    return run_id


async def _start(
    thread_id: str,
    *humans: dict[str, JsonValue],
    configurable: dict[str, JsonValue] | None = None,
    requested_ago: timedelta = timedelta(0),
) -> str:
    """Start a run of the thread for ``humans``; returns its run id."""
    run = RemoteRun(thread_id=thread_id, assistant_id="agent", configurable=configurable or {})
    run_id = _run_id(datetime.now(UTC) - requested_ago)
    started = RunStarted(run_id=run_id, humans=list(humans))
    await TokenRun.answer(_Agent(), run, "run_started", started.model_dump(mode="json"))
    await _settled()
    return run_id


async def _settled() -> None:
    while RunInterjections._pending:
        await asyncio.gather(*RunInterjections._pending)


def _sent(model: str | None, *, picked: bool = False) -> dict[str, JsonValue]:
    """A dashboard run's model settings: ``model``, or Auto when None."""
    selection: dict[str, JsonValue] = (
        {"model_selection": "auto"}
        if model is None
        else {"model_selection": "explicit", "agent_model_id": model, "agent_effort": "high"}
    )
    return {"source": "dashboard", **selection, "model_selection_changed": picked}


async def test_a_typed_message_that_cuts_off_a_working_run_supersedes_it(
    feedback: _Feedback,
) -> None:
    await _thread("thread-1")
    await _turn("thread-1", "completed", started_ago=timedelta(minutes=9))
    working = await _turn("thread-1", "interrupted", started_ago=timedelta(minutes=2))
    message = _typed("stop, use the other API")

    run_id = await _start("thread-1", message)

    assert feedback.of("thread-1") == [("human_interjection", 1.0, "superseded", "slack")]
    assert feedback.sources("thread-1", "human_interjection") == [
        {
            "thread_id": "thread-1",
            "run_id": run_id,
            "surface": "slack",
            "message_id": _id(message),
            "interrupted_run_id": working,
        }
    ]
    # It cut off a turn still working, so it answers no finished reply.
    assert feedback.of("thread-1", "human_follow_up") == []


@pytest.mark.parametrize(
    ("state", "started_ago", "stopped", "message"),
    [
        # The previous run had ended on its own.
        ("completed", timedelta(minutes=2), False, "next thing"),
        # A second send right behind the first.
        ("running", timedelta(seconds=3), False, "next thing"),
        # A person pressed Stop, then wrote.
        ("interrupted", timedelta(minutes=2), True, "next thing"),
        # A turn left running that recorded nothing for hours had stalled.
        ("running", timedelta(hours=3), False, "next thing"),
    ],
)
async def test_what_is_not_a_person_superseding_a_run(
    feedback: _Feedback, state: str, started_ago: timedelta, stopped: bool, message: str
) -> None:
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    await _thread("thread-1", **({STOPPED_AT_KEY: now_ms} if stopped else {}))
    await _turn("thread-1", state, started_ago=started_ago)

    await _start("thread-1", _typed(message))

    assert feedback.of("thread-1") == []


async def test_an_automation_woken_with_its_owners_config_is_not_a_person(
    feedback: _Feedback, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _thread("thread-1")
    await _turn("thread-1", "running", started_ago=timedelta(minutes=2))
    sent: list[RunInput] = []

    async def create_durable_run(_thread_id: str, _assistant: str, *, input: RunInput, **_: object):
        sent.append(input)
        return {"run_id": str(uuid4())}

    monkeypatch.setattr(dispatch, "create_durable_run", create_durable_run)
    pr = PullRequest(owner="lc", repo="repo", number=7, author="ada")
    request = HumanReviewRequest(
        pull_request_id=pr.id,
        head_sha="abc",
        kind="standard",
        thread_id="thread-1",
        run_config={"source": "slack", "github_login": "octocat"},
    )
    request.pull_request = pr
    unclaimed = prompt(
        "runs/human-review-unclaimed",
        pr_url=pr.url,
        minutes=30,
        author=pr.author,
        posted=False,
        asked=False,
        trigger=None,
        suggestions=[],
    )

    # Nobody picked a reviewer in time, so Robin wakes the thread that asked for one.
    assert await ReviewCard(request).notify_agent(unclaimed, title="Pick a reviewer")

    [run_input] = sent
    await _start("thread-1", *(_human(message) for message in run_input["messages"]))
    assert feedback.rows == {}


async def test_a_run_asked_for_right_behind_a_working_one_never_supersedes_it(
    feedback: _Feedback,
) -> None:
    await _thread("thread-1")
    await _turn("thread-1", "interrupted", started_ago=timedelta(minutes=2))

    # A second send right behind the first, whose run starts only once the first one ends.
    await _start("thread-1", _typed("and also"), requested_ago=timedelta(seconds=117))

    assert feedback.of("thread-1") == []


async def test_a_run_that_kept_working_for_hours_is_superseded(feedback: _Feedback) -> None:
    await _thread("thread-1")
    await _turn(
        "thread-1", "running", started_ago=timedelta(hours=3), active_ago=timedelta(minutes=1)
    )

    await _start("thread-1", _typed("stop, use the other API"))

    assert feedback.of("thread-1") == [("human_interjection", 1.0, "superseded", "slack")]


async def test_only_a_picker_action_on_a_follow_up_is_a_pick(feedback: _Feedback) -> None:
    await _thread("thread-1")
    # The opening message's model is the thread's first, not a change.
    await _start("thread-1", _typed("start", "web"), configurable=_sent("gpt-5.5", picked=True))
    await _turn("thread-1", "completed", started_ago=timedelta(minutes=5))
    # The composer sends the model it shows on every message, such as one the agent
    # switched the thread to, or a new workspace default.
    await _start("thread-1", _typed("more", "web"), configurable=_sent("claude-opus-5-5"))
    await _start("thread-1", _typed("go on", "web"), configurable=_sent(None))
    assert feedback.of("thread-1") == []

    await _start("thread-1", _typed("try gpt", "web"), configurable=_sent("gpt-5.5", picked=True))
    await _start("thread-1", _typed("back to auto", "web"), configurable=_sent(None, picked=True))

    assert feedback.of("thread-1") == [("human_interjection", 1.0, "model_pick", "web")] * 2
    picks = feedback.sources("thread-1", "human_interjection")
    # Neither pick cut a turn off.
    assert [source["interrupted_run_id"] for source in picks] == [None, None]


async def test_a_typed_message_a_model_call_took_mid_turn_steers_it(feedback: _Feedback) -> None:
    run = RemoteRun(thread_id="thread-1", assistant_id="agent", configurable={})

    async def answered(human: dict[str, JsonValue], *, mid_turn: bool) -> None:
        result = ModelResult(
            run_id="run-1",
            response=messages_to_dict([AIMessage(content="ok", id=str(uuid4()))]),
            humans=[human],
            mid_turn=mid_turn,
        )
        await TokenRun.answer(_Agent(), run, "model_result", result.model_dump(mode="json"))
        await _settled()

    # What the run's first model call answers is what the turn starts with.
    await answered(_typed("also check the docs", "web"), mid_turn=False)
    # Another agent thread relayed it.
    await answered(_typed("relayed note", relayed=True), mid_turn=True)
    assert feedback.of("thread-1") == []

    steer = _typed("no, the other file", "web")
    await answered(steer, mid_turn=True)

    assert feedback.of("thread-1") == [("human_interjection", 1.0, "steered", "web")]
    [source] = feedback.sources("thread-1", "human_interjection")
    assert (source["message_id"], source["interrupted_run_id"]) == (_id(steer), "run-1")


async def test_a_typed_message_after_a_completed_turn_follows_up_its_reply(
    feedback: _Feedback,
) -> None:
    await _thread("thread-1")
    await _turn("thread-1", "completed", started_ago=timedelta(minutes=9))
    answered = await _turn(
        "thread-1", "completed", started_ago=timedelta(minutes=5), ended_ago=timedelta(minutes=2)
    )
    message = _typed("no, keep the old name", "web")

    run_id = await _start("thread-1", message)

    assert feedback.sources("thread-1", "human_follow_up") == [
        {
            "thread_id": "thread-1",
            "run_id": run_id,
            "surface": "web",
            "message_id": _id(message),
            "answered_run_id": answered,
            "seconds_since_reply": pytest.approx(120, abs=5),
        }
    ]
    assert feedback.of("thread-1") == []


@pytest.mark.parametrize(
    ("state", "stopped", "requested_ago", "message"),
    [
        # Robin's turn failed rather than replied.
        ("failed", False, timedelta(0), "try again"),
        # A person pressed Stop, then wrote.
        ("interrupted", True, timedelta(0), "try again"),
        # Queued while the turn still worked, before its reply ended.
        ("completed", False, timedelta(minutes=3), "and also"),
    ],
)
async def test_what_is_not_a_person_following_up_a_reply(
    feedback: _Feedback, state: str, stopped: bool, requested_ago: timedelta, message: str
) -> None:
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    await _thread("thread-1", **({STOPPED_AT_KEY: now_ms} if stopped else {}))
    await _turn("thread-1", state, started_ago=timedelta(minutes=5), ended_ago=timedelta(minutes=2))

    await _start("thread-1", _typed(message), requested_ago=requested_ago)

    assert feedback.rows == {}


async def test_approving_a_blocked_workflow_push_is_not_a_person_following_up(
    feedback: _Feedback, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _thread("thread-1")
    await _turn(
        "thread-1", "completed", started_ago=timedelta(minutes=5), ended_ago=timedelta(minutes=2)
    )
    sent: list[RunInput] = []

    async def create_durable_run(_thread_id: str, _assistant: str, *, input: RunInput, **_: object):
        sent.append(input)
        return {"run_id": str(uuid4())}

    monkeypatch.setattr(dispatch, "create_durable_run", create_durable_run)

    await workflow_approval_api.dispatch_followup(
        "thread-1",
        {"source": "slack", "github_login": "octocat"},
        "The workflow-file push approval was approved. Retry the blocked git push now.",
        github_login="octocat",
    )

    [run_input] = sent
    await _start("thread-1", *(_human(message) for message in run_input["messages"]))
    assert feedback.rows == {}


async def test_both_routing_arms_record_the_same_step_ins(feedback: _Feedback) -> None:
    performance, balanced = (
        next(f"{tier}-{n}" for n in range(100) if RoutingArm.tier(f"{tier}-{n}") == tier)
        for tier in ("performance", "balanced")
    )
    for thread_id in (performance, balanced):
        await _thread(thread_id)
        await _start(thread_id, _typed("start", "web"), configurable=_sent("gpt-5.5", picked=True))
        await _turn(
            thread_id, "completed", started_ago=timedelta(minutes=5), ended_ago=timedelta(minutes=1)
        )
        # Slack sends the thread's pinned model, and the composer the one it shows.
        slack = {"source": "slack", "model_selection": "explicit", "agent_model_id": "gpt-5.5"}
        await _start(thread_id, _typed("from slack"), configurable=slack)
        await _start(thread_id, _typed("more", "web"), configurable=_sent("gpt-5.5"))
        working = await _turn(thread_id, "running", started_ago=timedelta(minutes=1))
        switch = _typed("switch", "web")
        await _start(thread_id, switch, configurable=_sent("claude-opus-5-5", picked=True))
        # The pick and the cut-off it came with name the same message and turn.
        sources = feedback.sources(thread_id, "human_interjection")
        assert {(s["message_id"], s["interrupted_run_id"]) for s in sources} == {
            (_id(switch), working)
        }

    assert feedback.of(performance) == feedback.of(balanced)
    assert [row[2] for row in feedback.of(performance)] == ["model_pick", "superseded"]
    assert feedback.of(performance, "human_follow_up") == feedback.of(balanced, "human_follow_up")
    assert [row[3] for row in feedback.of(performance, "human_follow_up")] == ["slack", "web"]


# A message that supersedes the working turn, and one that follows up its reply.
@pytest.mark.parametrize("state", ["running", "completed"])
async def test_a_failed_feedback_write_leaves_the_run_alone(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, state: str
) -> None:
    async def unreachable(*_args: object, **_kwargs: object) -> bool:
        raise ConnectionError("LangSmith is down")

    monkeypatch.setattr(
        "openswe.threads.interjections.create_langsmith_thread_feedback", unreachable
    )
    await _thread("thread-1")
    await _turn("thread-1", state, started_ago=timedelta(minutes=2), ended_ago=timedelta(minutes=1))

    with caplog.at_level(logging.ERROR):
        await _start("thread-1", _typed("stop"))

    assert "Could not record feedback on a person's message" in caplog.text


async def test_a_slow_feedback_write_does_not_hold_up_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = asyncio.Event()

    async def stalled(*_args: object, **_kwargs: object) -> bool:
        await release.wait()
        return True

    monkeypatch.setattr("openswe.threads.interjections.create_langsmith_thread_feedback", stalled)
    await _thread("thread-1")
    await _turn("thread-1", "running", started_ago=timedelta(minutes=2))
    run = RemoteRun(thread_id="thread-1", assistant_id="agent", configurable={})
    started = RunStarted(run_id="run-1", humans=[_typed("stop")])

    await asyncio.wait_for(
        TokenRun.answer(_Agent(), run, "run_started", started.model_dump(mode="json")), 5
    )

    release.set()
    await _settled()
