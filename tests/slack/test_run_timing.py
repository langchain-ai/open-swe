import json
import logging
from unittest.mock import AsyncMock

import pytest
from runtime_api.loop import FIRST_OUTPUT_AT_KEY, FIRST_TEXT_AT_KEY

from openswe.dashboard import ttft
from openswe.dashboard.ttft import DashboardSandbox
from openswe.slack import run_timing
from openswe.slack.run_timing import SlackRunTimeline

POSTED_TS = "1767225600.123456"  # 2026-01-01T00:00:00.123456Z
POSTED_MS = 1_767_225_600_123


def _reply(
    content: str, *, output_at: int, invocation: str = "inv-1", calls: tuple[str, ...] = ()
) -> dict[str, object]:
    """One of the agent's replies, as MDA stamps it, calling ``slack_reply`` once per id."""
    return {
        "type": "ai",
        "content": content,
        "tool_calls": [{"name": "slack_reply", "args": {}, "id": call} for call in calls],
        "response_metadata": {
            FIRST_OUTPUT_AT_KEY: output_at,
            FIRST_TEXT_AT_KEY: output_at,
            "open_swe_invocation_id": invocation,
        },
    }


def _posted(call: str, message_ts: str | None, *, images: bool = False) -> dict[str, object]:
    """``slack_reply``'s tool message: posted at ``message_ts``, or failed without one."""
    result = (
        {"success": True, "message_ts": message_ts}
        if message_ts
        else {"success": False, "error": "Message cannot be empty"}
    )
    content: object = json.dumps(result)
    if images:
        content = [{"type": "text", "text": content}, {"type": "image", "url": "x"}]
    return {"type": "tool", "name": "slack_reply", "tool_call_id": call, "content": content}


def _finished_run(
    messages: list[dict[str, object]],
    *,
    created_at: str = "2026-01-01T00:00:01+00:00",
    started_at: str = "2026-01-01T00:00:01+00:00",
    **metadata: object,
) -> dict[str, object]:
    return {
        "run_id": "run-1",
        "thread_id": "thread-1",
        "created_at": created_at,
        "run_started_at": started_at,
        "metadata": {"slack_trigger_ts": POSTED_TS, "invocation_id": "inv-1", **metadata},
        "values": {"messages": messages},
    }


def test_slack_ts_is_read_in_epoch_ms() -> None:
    assert SlackRunTimeline.ts_ms(POSTED_TS) == POSTED_MS
    assert SlackRunTimeline.ts_ms("") is None
    assert SlackRunTimeline.ts_ms("x") is None


def test_timeline_marks_first_output_and_the_first_reply_posted_in_slack() -> None:
    timeline = SlackRunTimeline(POSTED_MS, run_id="run-1")

    timeline.read(
        [
            # An earlier run's reply and post.
            _reply("Earlier", output_at=POSTED_MS - 60_000, invocation="inv-0", calls=("c0",)),
            _posted("c0", "1767225500.000000"),
            {"type": "human", "content": "<@U1> fix login"},
            # A post that failed shows nothing.
            _reply("", output_at=POSTED_MS + 4_000, calls=("c1",)),
            _posted("c1", None),
            _reply("", output_at=POSTED_MS + 6_000, calls=("c2",)),
            _posted("c2", "1767225607.623456", images=True),
            _reply("Done", output_at=POSTED_MS + 20_000, calls=("c3",)),
            _posted("c3", "1767225621.123456"),
            # Another tool's post is not a reply in the thread.
            {
                "type": "tool",
                "name": "slack_post_message",
                "tool_call_id": "c4",
                "content": json.dumps({"success": True, "message_ts": "1767225601.0"}),
            },
        ],
        invocation_id="inv-1",
    )

    assert timeline.seconds() == {"first_generation": 4.0, "first_reply": 7.5}


def test_replies_in_langchains_constructor_form_are_read() -> None:
    timeline = SlackRunTimeline(POSTED_MS, run_id="run-1")
    reply = _reply("", output_at=POSTED_MS + 2_000, calls=("c1",))
    posted = _posted("c1", "1767225603.123456")

    timeline.read(
        [
            {
                "lc": 1,
                "type": "constructor",
                "id": ["langchain", "schema", "messages", kind],
                "kwargs": body,
            }
            for kind, body in (("AIMessage", reply), ("ToolMessage", posted))
        ],
        invocation_id="inv-1",
    )

    assert timeline.seconds() == {"first_generation": 2.0, "first_reply": 3.0}


class _Recorded:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.latencies: list[tuple[dict[str, float], dict[str, str]]] = []

        def record(seconds: dict[str, float], *, attributes: dict[str, str]) -> None:
            self.latencies.append((dict(seconds), dict(attributes)))

        self.thread: dict[str, object] = {"sandbox_id": "sbx-1"}

        async def metadata(*_: object) -> dict[str, object]:
            return self.thread

        monkeypatch.setattr(ttft.DashboardRunTimeline, "record_latencies", staticmethod(record))
        monkeypatch.setattr(ttft.DashboardRunTimeline, "thread_metadata", staticmethod(metadata))
        self.marked = AsyncMock()
        monkeypatch.setattr(ttft.DashboardRunTimeline, "mark_timed", self.marked)


async def test_run_completion_times_the_slack_message_it_started(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    recorded = _Recorded(monkeypatch)
    run = _finished_run(
        [
            _reply("", output_at=POSTED_MS + 3_000, calls=("c1",)),
            _posted("c1", "1767225605.123456"),
        ]
    )

    with caplog.at_level(logging.INFO, logger=run_timing.__name__):
        await SlackRunTimeline.settle(run)

    assert recorded.latencies == [
        (
            {
                "open_swe_slack_thread_first_generation": 3.0,
                "open_swe_slack_thread_first_reply": 5.0,
            },
            {"source": "slack", "sandbox": DashboardSandbox.CLOUD.value},
        )
    ]
    (timing,) = [r for r in caplog.records if r.getMessage() == "Slack run timing"]
    assert (timing.first_generation_ms, timing.first_reply_ms) == (3_000, 5_000)  # type: ignore[attr-defined]
    recorded.marked.assert_awaited_once_with(
        "thread-1", recorded.thread, "run-1", key="slack_timed_run_ids"
    )


async def test_a_completion_delivered_again_times_the_message_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _Recorded(monkeypatch)
    recorded.thread["slack_timed_run_ids"] = ["run-1"]

    await SlackRunTimeline.settle(
        _finished_run(
            [
                _reply("", output_at=POSTED_MS + 3_000, calls=("c1",)),
                _posted("c1", "1767225605.123456"),
            ]
        )
    )

    assert recorded.latencies == []
    recorded.marked.assert_not_awaited()


@pytest.mark.parametrize(
    "run",
    [
        # No Slack message started it.
        {"run_id": "run-1", "thread_id": "thread-1", "metadata": {}, "values": {}},
        # A later run that inherited the message's metadata.
        _finished_run(
            [_reply("Hi", output_at=POSTED_MS + 600_000)], created_at="2026-01-01T00:10:00+00:00"
        ),
        # A dashboard send after the Slack message started it: the dashboard times it.
        _finished_run(
            [_reply("Hi", output_at=POSTED_MS + 61_000)],
            created_at="2026-01-01T00:01:00+00:00",
            dashboard_ttft_started_at_ms=POSTED_MS + 59_000,
        ),
    ],
)
async def test_runs_no_slack_message_just_started_are_not_timed(
    monkeypatch: pytest.MonkeyPatch, run: dict[str, object]
) -> None:
    recorded = _Recorded(monkeypatch)

    await SlackRunTimeline.settle(run)

    assert recorded.latencies == []


# B is posted at 0s while the run for an earlier message A (invocation inv-0) still
# works: that run replies at 30s, then B's run, queued at once, starts at 35s and
# replies at 50s.
_QUEUED_BEHIND_A = [
    _reply("Still on A", output_at=POSTED_MS + 29_000, invocation="inv-0", calls=("a1",)),
    _posted("a1", "1767225630.123456"),
    {"type": "human", "content": "<@U1> and B?"},
    _reply("On B", output_at=POSTED_MS + 45_000, calls=("b1",)),
    _posted("b1", "1767225650.123456"),
]


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        # Without the run's invocation, output from before the run started is not its.
        {"invocation_id": None},
    ],
)
async def test_a_queued_message_is_timed_from_its_own_run_not_the_one_it_waited_for(
    monkeypatch: pytest.MonkeyPatch, metadata: dict[str, object]
) -> None:
    recorded = _Recorded(monkeypatch)
    run = _finished_run(
        _QUEUED_BEHIND_A,
        created_at="2026-01-01T00:00:00.500+00:00",
        started_at="2026-01-01T00:00:35.123+00:00",
    )
    run_metadata = run["metadata"]
    assert isinstance(run_metadata, dict)
    for key, value in metadata.items():
        if value is None:
            run_metadata.pop(key)

    await SlackRunTimeline.settle(run)

    ((seconds, _),) = recorded.latencies
    assert seconds == {
        "open_swe_slack_thread_first_generation": 45.0,
        "open_swe_slack_thread_first_reply": 50.0,
    }


async def test_a_run_resumed_after_a_drain_keeps_its_earlier_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # It replied at 5s and 8s, drained at 20s, and its resumed attempt started at 25s.
    recorded = _Recorded(monkeypatch)
    run = _finished_run(
        [
            _reply("", output_at=POSTED_MS + 5_000, calls=("c1",)),
            _posted("c1", "1767225608.123456"),
            _reply("Done", output_at=POSTED_MS + 39_000, calls=("c2",)),
            _posted("c2", "1767225640.123456"),
        ],
        started_at="2026-01-01T00:00:25.123+00:00",
    )

    await SlackRunTimeline.settle(run)

    ((seconds, _),) = recorded.latencies
    assert seconds == {
        "open_swe_slack_thread_first_generation": 5.0,
        "open_swe_slack_thread_first_reply": 8.0,
    }


async def test_slack_latencies_go_to_their_own_histograms(monkeypatch: pytest.MonkeyPatch) -> None:
    from langgraph_api import metrics_otlp

    recorded: list[tuple[str, float, dict[str, str]]] = []

    class Reporter:
        def record_latency(
            self, metric: object, seconds: float, *, attributes: dict[str, str]
        ) -> None:
            recorded.append((metric.name, seconds, attributes))  # type: ignore[attr-defined]

    monkeypatch.setattr(metrics_otlp, "get_otlp_metrics_reporter", Reporter)
    timeline = SlackRunTimeline(POSTED_MS, run_id="run-1")
    timeline.marks = {"first_reply": POSTED_MS + 2_500}

    timeline.record(thread_id="thread-1", sandbox=DashboardSandbox.BRIDGE)

    assert recorded == [
        ("lg_api_open_swe_slack_thread_first_reply", 2.5, {"source": "slack", "sandbox": "bridge"})
    ]


def test_dashboard_marks_keep_the_earliest_across_reads() -> None:
    timeline = ttft.DashboardRunTimeline(POSTED_MS, run_id="run-1")

    timeline.read([_reply("Hi", output_at=POSTED_MS + 2_000)])
    timeline.read([_reply("Later", output_at=POSTED_MS + 5_000)])

    assert timeline.seconds() == {"first_generation": 2.0, "ttft": 2.0}


async def test_unreadable_invocation_metadata_is_reported_and_falls_back_to_the_start(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    recorded = _Recorded(monkeypatch)
    run = _finished_run(
        _QUEUED_BEHIND_A,
        created_at="2026-01-01T00:00:00.500+00:00",
        started_at="2026-01-01T00:00:35.123+00:00",
        prepare_run_id="inv-other",
    )

    with caplog.at_level(logging.WARNING, logger=run_timing.__name__):
        await SlackRunTimeline.settle(run)

    assert any(
        r.getMessage() == "Slack run timing found unreadable invocation metadata"
        for r in caplog.records
    )
    ((seconds, _),) = recorded.latencies
    assert seconds == {
        "open_swe_slack_thread_first_generation": 45.0,
        "open_swe_slack_thread_first_reply": 50.0,
    }


async def test_a_slack_run_that_inherited_a_dashboard_sends_metadata_is_timed_as_slack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A dashboard send 30s before the message left its metadata on this run, and the
    # dashboard timing reported this run already under its own record.
    recorded = _Recorded(monkeypatch)
    recorded.thread["timed_run_ids"] = ["run-1"]
    run = _finished_run(
        [
            _reply("", output_at=POSTED_MS + 3_000, calls=("c1",)),
            _posted("c1", "1767225605.123456"),
        ],
        dashboard_ttft_started_at_ms=POSTED_MS - 30_000,
    )

    assert ttft.DashboardRunTimeline.of_run(run) is None
    await SlackRunTimeline.settle(run)

    ((seconds, _),) = recorded.latencies
    assert seconds == {
        "open_swe_slack_thread_first_generation": 3.0,
        "open_swe_slack_thread_first_reply": 5.0,
    }
