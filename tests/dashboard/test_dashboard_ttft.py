import json
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote
from uuid import UUID, uuid5

import pytest
from runtime_api.loop import FIRST_OUTPUT_AT_KEY, FIRST_TEXT_AT_KEY

from openswe.dashboard import ttft
from openswe.trace_metadata import RunOrigin

SENT_AT = 1_767_225_600_000  # 2026-01-01T00:00:00Z


def _reply(content: str, *, output_at: int, text_at: int | None = None) -> dict[str, object]:
    stamps = {FIRST_OUTPUT_AT_KEY: output_at}
    if text_at is not None:
        stamps[FIRST_TEXT_AT_KEY] = text_at
    return {"type": "ai", "content": content, "response_metadata": stamps}


def _finished_run(
    messages: list[dict[str, object]],
    *,
    created_at: str = "2026-01-01T00:00:01.500+00:00",
    error: str | None = None,
) -> dict[str, object]:
    return {
        "run_id": "run-1",
        "thread_id": "thread-1",
        "created_at": created_at,
        "run_ended_at": "2026-01-01T00:01:00+00:00",
        "metadata": {
            "dashboard_ttft_started_at_ms": SENT_AT,
            "dashboard_submitted_at_ms": SENT_AT - 300,
            "dashboard_client": "web",
            "dashboard_send_trace_id": "0194a9b2-0000-7000-8000-000000000001",
            "dashboard_send_dotted_order": (
                "20251231T235959700000Z0194a9b2-0000-7000-8000-000000000001"
            ),
        },
        "values": {"messages": messages},
        "error": error,
    }


def test_timeline_marks_the_earliest_output_and_text_since_the_send() -> None:
    timeline = ttft.DashboardRunTimeline(SENT_AT, run_id="run-1")

    timeline.read(
        [
            # An earlier run's reply.
            _reply("Earlier", output_at=SENT_AT - 60_000, text_at=SENT_AT - 59_000),
            {"type": "human", "content": "Fix login"},
            # The run opens with a tool call, so its first text comes later.
            _reply("", output_at=SENT_AT + 9_000),
            {"type": "tool", "content": "ok", "response_metadata": {}},
            {
                "lc": 1,
                "type": "constructor",
                "id": ["langchain", "schema", "messages", "AIMessage"],
                "kwargs": {
                    "content": "Done",
                    "response_metadata": {
                        FIRST_OUTPUT_AT_KEY: SENT_AT + 29_000,
                        FIRST_TEXT_AT_KEY: SENT_AT + 30_000,
                    },
                },
            },
            _reply("More", output_at=SENT_AT + 40_000, text_at=SENT_AT + 41_000),
        ]
    )

    assert timeline.seconds() == {"first_generation": 9.0, "ttft": 30.0}


class _Recorded:
    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, *, started_run_id: str | None = "run-1"
    ) -> None:
        self.histograms: list[dict[str, float]] = []
        thread: dict[str, object] = {"sandbox_id": "sbx-1"}
        self.thread = thread
        if started_run_id is not None:
            # An older send's run, this send's, and a newer send's that started before
            # this run's completion was heard.
            thread["dashboard_send_runs"] = {
                str(SENT_AT - 600_000): "run-old",
                str(SENT_AT): started_run_id,
                str(SENT_AT + 60_000): "run-newer",
            }

        def record(timeline: ttft.DashboardRunTimeline, seconds: dict[str, float]) -> None:
            self.histograms.append(dict(seconds))

        async def metadata(*_: object) -> dict[str, object]:
            return thread

        monkeypatch.setattr(ttft.DashboardRunTimeline, "_record_histograms", record)
        monkeypatch.setattr(ttft.DashboardRunTimeline, "thread_metadata", staticmethod(metadata))
        self.marked = AsyncMock()
        monkeypatch.setattr(ttft.DashboardRunTimeline, "mark_timed", self.marked)


class _RecordingClient:
    """LangSmith as the send trace reaches it: runs posted, roots closed and read back."""

    def __init__(self) -> None:
        self.runs: list[dict[str, object]] = []
        self.updates: list[tuple[str, dict[str, object]]] = []
        self.ended_at: datetime | None = None

    def create_run(self, **run: object) -> None:
        self.runs.append(run)

    def update_run(self, run_id: str, **fields: object) -> None:
        self.updates.append((run_id, fields))

    @property
    def async_runs(self) -> SimpleNamespace:
        return SimpleNamespace(retrieve=self._retrieve)

    async def _retrieve(self, run_id: str, **_: object) -> SimpleNamespace:
        return SimpleNamespace(id=run_id, end_time=self.ended_at)


@pytest.fixture
def langsmith(monkeypatch: pytest.MonkeyPatch) -> _RecordingClient:
    client = _RecordingClient()
    monkeypatch.setenv("LANGSMITH_API_KEY", "deployment-key")
    monkeypatch.setattr(ttft, "tracing_is_enabled", lambda: True)
    monkeypatch.setattr(ttft, "sync_langsmith_client", lambda *_: client)
    monkeypatch.setattr(
        ttft, "async_langsmith_client", lambda *_: SimpleNamespace(runs=client.async_runs)
    )
    monkeypatch.setattr(ttft, "resolve_project_id_by_name", AsyncMock(return_value="project-id"))
    monkeypatch.setattr(ttft.MdaRuntime, "owner_ls_user_id", AsyncMock(return_value="ls-user-1"))
    return client


@pytest.mark.parametrize(
    "origin", [RunOrigin.DASHBOARD, RunOrigin.SLACK, RunOrigin.API, RunOrigin.AUTOMATION]
)
async def test_send_trace_traces_the_commands_request_as_it_runs(
    monkeypatch: pytest.MonkeyPatch, langsmith: _RecordingClient, origin: RunOrigin
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    messages = {"messages": [{"type": "human", "content": "Fix login"}]}
    trace = await ttft.DashboardSendTrace.open(
        thread_id="thread-1",
        client=ttft.DashboardClient.DESKTOP,
        started_at_ms=SENT_AT - 300,
        received_at_ms=SENT_AT,
        inputs=messages,
        origin=origin,
    )
    assert trace is not None

    # The span of the request that starts the run opens as the send arrives.
    (commands,) = langsmith.runs
    assert commands["name"] == "commands"
    assert commands["parent_run_id"] == trace.root.id
    assert commands["start_time"] == ttft.DashboardSendTrace.at(SENT_AT)
    with trace.stage("build_run"):
        pass
    (build,) = [run for run in langsmith.runs if run["name"] == "build_run"]
    assert build["parent_run_id"] == commands["id"]

    # Once the run is built, the root is posted naming it for the thread view, cost
    # lookups and personal trace access. Any update would end it, so it gets none.
    await trace.adopt(thread_id="thread-1", invocation_id="invocation-1")
    (root,) = [run for run in langsmith.runs if run["name"] == "dashboard.send"]
    assert root["inputs"] == messages
    assert root["extra"]["metadata"] == {  # type: ignore[index]
        "thread_id": "thread-1",
        "dashboard_client": "desktop",
        "origin": origin.value,
        "invocation_id": "invocation-1",
        "owner_ls_user_id": "ls-user-1",
    }
    assert root.get("end_time") is None
    assert _updates_of(langsmith, trace.trace_id) == []

    trace.end_commands()
    (ended,) = _updates_of(langsmith, commands["id"])
    assert ended["end_time"] is not None
    assert trace.run_metadata() == {
        "dashboard_send_trace_id": trace.trace_id,
        "dashboard_send_dotted_order": trace.root.dotted_order,
    }
    # The run's start carries where to nest and the mark MDA nests on, not the metadata.
    headers = trace.headers()
    assert headers["langsmith-trace"] == trace.root.dotted_order
    assert headers["baggage"].startswith(
        "langsmith-metadata=" + quote(json.dumps({"ls_nest_under_parent": True}))
    )
    assert "thread-1" not in headers["baggage"]


async def test_a_send_whose_run_is_never_built_posts_its_trace_closed(
    langsmith: _RecordingClient,
) -> None:
    trace = await ttft.DashboardSendTrace.open(
        thread_id="thread-1",
        client=ttft.DashboardClient.WEB,
        started_at_ms=SENT_AT,
        received_at_ms=SENT_AT,
        inputs={},
    )
    assert trace is not None

    trace.fail("The run could not be built (HTTPException)")

    (root,) = [run for run in langsmith.runs if run["name"] == "dashboard.send"]
    assert root["error"] == "The run could not be built (HTTPException)"
    assert trace.root.metadata["origin"] == "dashboard"
    assert root["end_time"] is not None
    commands = next(run for run in langsmith.runs if run["name"] == "commands")
    (ended,) = _updates_of(langsmith, commands["id"])
    assert ended["error"] == "The run could not be built (HTTPException)"


def _updates_of(langsmith: _RecordingClient, run_id: object) -> list[dict[str, object]]:
    return [fields for updated, fields in langsmith.updates if str(updated) == str(run_id)]


async def test_run_completion_times_the_send_from_its_replies_and_closes_its_trace(
    monkeypatch: pytest.MonkeyPatch,
    langsmith: _RecordingClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    recorded = _Recorded(monkeypatch)
    run = _finished_run(
        [
            {"type": "human", "content": "Fix login"},
            _reply("", output_at=SENT_AT + 9_000),
            _reply("Done", output_at=SENT_AT + 29_000, text_at=SENT_AT + 30_000),
        ]
    )
    run["metadata"].update(  # type: ignore[union-attr]
        {
            "billing_bucket": "user",
            "billed_user_email": "ada@example.com",
            "user_email": "ada@example.com",
            "kind": "agent",
            "event_match_ids": ["not-a-root-key"],
        }
    )

    with caplog.at_level(logging.INFO, logger=ttft.__name__):
        await ttft.DashboardRunTimeline.settle(run, failed=False)

    assert recorded.histograms == [{"first_generation": 9.0, "ttft": 30.0}]
    (timing,) = [r for r in caplog.records if r.getMessage() == "Dashboard run timing"]
    assert (timing.first_generation_ms, timing.ttft_ms) == (9_000, 30_000)  # type: ignore[attr-defined]
    assert timing.dashboard_client == "web"  # type: ignore[attr-defined]

    trace_id = "0194a9b2-0000-7000-8000-000000000001"
    # ``commands`` is the request's own span, traced as it ran.
    assert [(span["name"], span["outputs"]) for span in langsmith.runs] == [
        ("client.request", {"elapsed_ms": 300}),
        ("first_generation", {"elapsed_ms": 7_500}),
        ("first_text", {"elapsed_ms": 21_000}),
        # The whole wait for the first text, the one span to chart responsiveness by.
        ("time_to_first_reply", {"elapsed_ms": 30_000}),
    ]
    assert all(str(span["trace_id"]) == trace_id for span in langsmith.runs)
    assert all(
        str(span["dotted_order"]).startswith(f"20251231T235959700000Z{trace_id}.")
        for span in langsmith.runs
    )
    # Each stage charts by client on its own, without its root.
    assert all(
        span["extra"]["metadata"]  # type: ignore[index]
        == {
            "thread_id": "thread-1",
            "dashboard_client": "web",
            "agent_run_id": "run-1",
        }
        for span in langsmith.runs
    )
    ((closed, fields),) = langsmith.updates
    assert closed == trace_id
    assert fields["end_time"] == ttft.DashboardSendTrace.at(SENT_AT + 60_000)
    assert fields["outputs"] == {"messages": [run["values"]["messages"][-1]]}  # type: ignore[index]
    assert fields["error"] is None
    # The root bills and counts the send as the run it started, for charts of root traces.
    assert fields["extra"] == {
        "metadata": {
            "thread_id": "thread-1",
            "dashboard_client": "web",
            "billing_bucket": "user",
            "billed_user_email": "ada@example.com",
            "user_email": "ada@example.com",
            "kind": "agent",
        }
    }


async def test_a_run_that_gave_no_output_traces_only_its_request_stages(
    monkeypatch: pytest.MonkeyPatch, langsmith: _RecordingClient
) -> None:
    recorded = _Recorded(monkeypatch)

    await ttft.DashboardRunTimeline.settle(
        _finished_run([{"type": "human"}], error="RuntimeError('boom')"), failed=True
    )

    assert recorded.histograms == []
    assert [span["name"] for span in langsmith.runs] == ["client.request"]
    ((_, fields),) = langsmith.updates
    assert fields["error"] == "RuntimeError('boom')"


async def test_a_later_run_that_inherited_a_sends_metadata_is_not_timed_by_it(
    monkeypatch: pytest.MonkeyPatch, langsmith: _RecordingClient
) -> None:
    # A queued follow-up's run, created right after, sees the send's own replies too.
    recorded = _Recorded(monkeypatch, started_run_id="run-0")

    await ttft.DashboardRunTimeline.settle(
        _finished_run([_reply("Done", output_at=SENT_AT + 9_000, text_at=SENT_AT + 9_000)]),
        failed=False,
    )

    assert recorded.histograms == []
    assert langsmith.runs == []
    assert langsmith.updates == []


async def test_without_the_threads_record_a_run_created_long_after_the_send_is_not_timed(
    monkeypatch: pytest.MonkeyPatch, langsmith: _RecordingClient
) -> None:
    recorded = _Recorded(monkeypatch, started_run_id=None)

    await ttft.DashboardRunTimeline.settle(
        _finished_run(
            [_reply("Done", output_at=SENT_AT + 600_000, text_at=SENT_AT + 600_000)],
            created_at="2026-01-01T00:10:00+00:00",
        ),
        failed=False,
    )

    assert recorded.histograms == []
    assert langsmith.runs == []
    assert langsmith.updates == []


async def test_a_run_no_dashboard_send_started_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, langsmith: _RecordingClient
) -> None:
    recorded = _Recorded(monkeypatch)
    run = _finished_run([_reply("Done", output_at=SENT_AT, text_at=SENT_AT)])
    run["metadata"] = {}

    await ttft.DashboardRunTimeline.settle(run, failed=False)

    assert recorded.histograms == []
    assert langsmith.runs == langsmith.updates == []


@pytest.mark.parametrize(
    ("header", "expected"),
    [("999700", 999_700), ("1000500", 1_000_000), ("1003000", None), ("900000", None), ("x", None)],
)
def test_client_send_time_is_believed_only_near_the_backend_clock(
    header: str, expected: int | None
) -> None:
    assert ttft.DashboardRunTimeline.client_submitted_at(header, 1_000_000) == expected


class _Reporter:
    def __init__(self) -> None:
        self.latencies: list[tuple[str, float, dict[str, str]]] = []

    def record_latency(self, metric: object, seconds: float, *, attributes: dict[str, str]) -> None:
        self.latencies.append((metric.name, seconds, attributes))  # type: ignore[attr-defined]


async def test_each_mark_goes_to_its_histogram_labelled_by_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langgraph_api import metrics_otlp

    reporter = _Reporter()
    monkeypatch.setattr(metrics_otlp, "get_otlp_metrics_reporter", lambda: reporter)
    timeline = ttft.DashboardRunTimeline(
        SENT_AT, run_id="run-1", client=ttft.DashboardClient.DESKTOP
    )
    timeline.read([_reply("Hi", output_at=SENT_AT + 1_500, text_at=SENT_AT + 2_000)])

    timeline.report(thread_id="thread-1")

    labels = {"source": "dashboard", "client": "desktop"}
    assert sorted(reporter.latencies) == [
        ("lg_api_open_swe_dashboard_thread_first_generation", 1.5, labels),
        ("lg_api_open_swe_dashboard_thread_ttft", 2.0, labels),
    ]


def test_the_thread_keeps_the_runs_its_latest_sends_started() -> None:
    thread: dict[str, object] = {
        "dashboard_send_runs": {str(at): f"run-{at}" for at in range(1, 9)}
    }

    runs = ttft.DashboardRunTimeline.started(thread, received_at_ms=100, run_id="run-100")

    # The newest eight, a send whose completion is not heard yet among them.
    assert runs == {**{str(at): f"run-{at}" for at in range(2, 9)}, "100": "run-100"}


@pytest.mark.parametrize(
    ("reported", "closed"),
    [
        # Both were done the first time.
        (True, True),
        # The first delivery reported the marks, but closing the trace failed.
        (True, False),
        # The first delivery could not read the thread, but closed the trace.
        (False, True),
    ],
)
async def test_a_completion_delivered_again_does_only_what_the_first_did_not(
    monkeypatch: pytest.MonkeyPatch, langsmith: _RecordingClient, reported: bool, closed: bool
) -> None:
    recorded = _Recorded(monkeypatch)
    if reported:
        recorded.thread["timed_run_ids"] = ["run-0", "run-1"]
    if closed:
        langsmith.ended_at = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)

    await ttft.DashboardRunTimeline.settle(
        _finished_run([_reply("Done", output_at=SENT_AT + 9_000, text_at=SENT_AT + 9_000)]),
        failed=False,
    )

    assert bool(recorded.histograms) is not reported
    assert bool(recorded.marked.await_count) is not reported
    assert bool(langsmith.updates) is not closed
    # Stages traced again keep their ids, so they rewrite the spans already posted.
    trace_id = UUID("0194a9b2-0000-7000-8000-000000000001")
    assert [span["id"] for span in langsmith.runs] == (
        []
        if closed
        else [
            uuid5(trace_id, name)
            for name in (
                "client.request",
                "first_generation",
                "first_text",
                "time_to_first_reply",
            )
        ]
    )


async def test_a_timed_run_is_recorded_on_its_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    updates: list[tuple[str, dict[str, object]]] = []

    class Threads:
        async def update(self, thread_id: str, metadata: dict[str, object]) -> None:
            updates.append((thread_id, metadata))

    from openswe.threads import store

    monkeypatch.setattr(store, "THREADS", Threads())
    thread = {"timed_run_ids": [f"run-{i}" for i in range(16)]}

    await ttft.DashboardRunTimeline.mark_timed("thread-1", thread, "run-new")

    assert updates == [
        ("thread-1", {"timed_run_ids": [*(f"run-{i}" for i in range(1, 16)), "run-new"]})
    ]
