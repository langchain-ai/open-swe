from datetime import UTC, datetime, timedelta
from typing import cast

import langsmith.run_trees as run_trees
import pytest
import rt.send_trace as send_trace
from langsmith import tracing_context
from managed_deepagents._channels.http.endpoint import EndpointClient
from rt.intake import ask_backend
from rt.send_trace import SlackSendTrace
from runtime_api.slack import (
    SEND_DOTTED_ORDER_KEY,
    SEND_PROJECT_KEY,
    SEND_TRACE_ID_KEY,
    START_SLACK_TURN,
    SlackTurnRun,
    SlackTurnStarted,
)

POSTED_TS = "1767225600.123456"  # 2026-01-01T00:00:00.123456Z
POSTED = datetime(2026, 1, 1, 0, 0, 0, 123456, tzinfo=UTC)


class _LangSmith:
    """LangSmith as the trace reaches it: each run posted, then each update."""

    def __init__(self) -> None:
        self.posted: list[dict[str, object]] = []
        self.patched: list[dict[str, object]] = []

    def create_run(self, **run: object) -> None:
        self.posted.append(run)

    def update_run(self, run_id: object, **fields: object) -> None:
        self.patched.append({"id": run_id, **fields})

    def named(self, name: str) -> dict[str, object]:
        (run,) = [run for run in self.posted if run["name"] == name]
        return run


@pytest.fixture
def langsmith(monkeypatch: pytest.MonkeyPatch) -> _LangSmith:
    client = _LangSmith()
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setattr(send_trace, "tracing_is_enabled", lambda: True)
    monkeypatch.setattr(run_trees, "get_cached_client", lambda: client)
    return client


def _opened() -> SlackSendTrace:
    received = POSTED + timedelta(seconds=2)
    return SlackSendTrace.open(
        thread_id="thread-1",
        channel_id="C1",
        message_ts=POSTED_TS,
        text="<@UBOT> fix login",
        received_at=received,
        facts=(received, received + timedelta(milliseconds=80)),
    )


def _run() -> SlackTurnRun:
    return SlackTurnRun(
        input={},
        context={},
        metadata={"invocation_id": "inv-1", "owner_ls_user_id": "ls-1", "visibility": "private"},
    )


async def test_a_turn_traces_each_step_and_hands_the_trace_to_its_run(
    monkeypatch: pytest.MonkeyPatch, langsmith: _LangSmith
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.example")
    asked: list[dict[str, str]] = []

    async def answered(self: EndpointClient, msg_id: str, body: bytes, seconds: float) -> object:
        asked.append(dict(self._headers))  # noqa: SLF001
        return SlackTurnStarted().model_dump(mode="json")

    monkeypatch.setattr(EndpointClient, "_post", answered)
    trace = _opened()
    root = trace.root
    assert root is not None
    # As the deployment traces: langsmith caches the environment it read first.
    with tracing_context(enabled=True), trace.step("start_slack_turn"):
        await ask_backend(START_SLACK_TURN, START_SLACK_TURN.request.model_construct(), msg_id="m1")
    run = _run()

    trace.started(run, thread_id="thread-2")

    # Each span is posted as it starts: the handover from Slack, the handler, and its steps.
    delivery = langsmith.named("delivery")
    assert (delivery["start_time"], delivery["end_time"]) == (
        POSTED,
        POSTED + timedelta(seconds=2),
    )
    facts = langsmith.named("slack_message_facts")
    assert facts["parent_run_id"] == langsmith.named("channel")["id"]
    step = langsmith.named("start_slack_turn")
    # The backend's answer nests under the step that asked.
    (headers,) = asked
    assert headers["langsmith-trace"] == step["dotted_order"]
    assert "langsmith-metadata" not in headers.get("baggage", "")
    # The run names the trace for its completion to close; the root, posted once, stays open
    # and names the run's thread and invocation, and who may read it.
    assert run.metadata[SEND_TRACE_ID_KEY] == str(root.id)
    assert run.metadata[SEND_DOTTED_ORDER_KEY] == root.dotted_order
    # The backend may trace into another project: it closes the trace in this one.
    assert run.metadata[SEND_PROJECT_KEY] == root.session_name
    posted_root = langsmith.named("slack.send")
    assert posted_root["start_time"] == POSTED
    assert posted_root.get("end_time") is None
    assert cast(dict[str, dict[str, object]], posted_root["extra"])["metadata"] == {
        "thread_id": "thread-2",
        "slack_channel_id": "C1",
        "slack_message_ts": POSTED_TS,
        "origin": "slack",
        "invocation_id": "inv-1",
        "owner_ls_user_id": "ls-1",
        "visibility": "private",
    }
    assert str(root.id) not in {str(update["id"]) for update in langsmith.patched}
    # The handler's span ends as it hands the run over.
    channel_id = langsmith.named("channel")["id"]
    assert any(
        str(update["id"]) == str(channel_id) and update["end_time"] for update in langsmith.patched
    )


def test_a_message_that_starts_no_run_closes_its_trace(langsmith: _LangSmith) -> None:
    trace = _opened()

    trace.no_run("declined: sign_in")
    trace.no_run("again")

    root = langsmith.named("slack.send")
    assert root["end_time"] is not None
    assert root["outputs"] == {"outcome": "declined: sign_in"}
    # No run names the thread's visibility, so the message is hidden as private.
    assert cast(dict[str, dict[str, object]], root["extra"])["metadata"]["visibility"] == "private"


def test_a_deployment_that_does_not_trace_leaves_the_run_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(send_trace, "tracing_is_enabled", lambda: False)
    trace = _opened()
    run = _run()

    with trace.step("start_slack_turn"):
        pass
    trace.started(run, thread_id="thread-1")

    assert trace.root is None
    assert run.metadata == _run().metadata
