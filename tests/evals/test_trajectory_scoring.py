"""``evals/trajectories/scoring.py`` reads the failure modes of RBN-211 off a run's trace."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from langsmith.schemas import Run

from evals.trajectories.scoring import Trajectory

_START = datetime(2026, 11, 18, 12, tzinfo=UTC)
_TRACE = uuid4()


def _run(
    name: str,
    kind: str,
    at: float,
    seconds: float,
    *,
    inputs: dict[str, object] | None = None,
    outputs: dict[str, object] | None = None,
    error: str | None = None,
    agent_type: str | None = None,
) -> Run:
    return Run(
        id=uuid4(),
        trace_id=_TRACE,
        name=name,
        run_type=kind,
        start_time=_START + timedelta(seconds=at),
        end_time=_START + timedelta(seconds=at + seconds),
        inputs=inputs or {},
        outputs=outputs or {},
        error=error,
        extra={"metadata": {"ls_agent_type": agent_type}} if agent_type else {},
    )


def _model(at: float, *, text: str = "", calls: int = 0, agent_type: str | None = None) -> Run:
    message = {
        "type": "ai",
        "content": text,
        "tool_calls": [{"name": "execute", "args": {}, "id": str(i)} for i in range(calls)],
    }
    return _run(
        "ChatAnthropic",
        "llm",
        at,
        2,
        outputs={"generations": [[{"message": message}]]},
        agent_type=agent_type,
    )


def test_a_run_that_blocks_on_a_long_sleep_and_stalls_in_code_mode_scores_both() -> None:
    runs = [
        _model(0, calls=1),
        _run("execute", "tool", 2, 1500, inputs={"command": "sleep 1500; tail -5 build.log"}),
        _run("eval", "tool", 1502, 300, inputs={"code": "Object.keys(r)"}),
        _model(1802, text="Done: the build passed."),
    ]

    scores = Trajectory.of(runs).scores()

    assert scores["blocking_execute_seconds"] == 1500
    assert scores["long_sleeps"] == 1
    assert scores["used_background"] is False
    assert scores["slow_eval_seconds"] == 300
    assert scores["replied"] is True
    assert scores["first_reply_seconds"] == 1802


def test_retrying_the_same_failing_call_and_never_answering_is_scored() -> None:
    search = {"query": "robin team threads"}
    runs = [
        _model(0, calls=1),
        *(
            _run("list_threads", "tool", 2 + i * 120, 120, inputs=search, error="TimeoutError")
            for i in range(3)
        ),
        _run("background_execute", "tool", 400, 1, inputs={"command": "make test"}),
    ]

    scores = Trajectory.of(runs).scores()

    assert scores["most_repeated_failure"] == 3
    assert scores["used_background"] is True
    assert scores["replied"] is False
    assert scores["first_reply_seconds"] == -1


def test_returned_tool_failures_count_as_retries() -> None:
    runs = [
        _run(
            "execute",
            "tool",
            2 * i,
            1,
            inputs={"command": "false"},
            outputs={"output": output},
        )
        for i, output in enumerate(
            (
                {"type": "tool", "status": "error"},
                {"type": "tool", "status": "success", "content": '{"success": false}'},
                {"update": {"messages": [{"type": "tool", "artifact": {"exit_code": 1}}]}},
                {"type": "tool", "artifact": {"exit_code": 0}},
                {"type": "tool", "artifact": {"exit_code": 1}},
            )
        )
    ]

    assert Trajectory.of(runs).most_repeated_failure == 3


def test_only_a_delivered_slack_reply_or_root_model_answer_counts() -> None:
    runs = [
        _model(0, calls=1, agent_type="root"),
        _model(2, text="Internal report", agent_type="subagent"),
        _run("slack_reply", "tool", 4, 1, error="TimeoutError"),
        _run("slack_reply", "tool", 6, 1, outputs={"output": {"success": False}}),
    ]

    assert Trajectory.of(runs).scores()["replied"] is False
    retry = _run("slack_reply", "tool", 8, 1, outputs={"output": {"success": True}})
    assert Trajectory.of([*runs, retry]).scores()["first_reply_seconds"] == 8
    root_answer = _model(10, text="Done", agent_type="root")
    assert Trajectory.of([*runs, root_answer]).scores()["first_reply_seconds"] == 10
