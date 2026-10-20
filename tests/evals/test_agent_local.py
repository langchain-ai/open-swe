"""Local experiments keep their denominator, task identity, and raw evidence."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from evals.agent import run_local
from evals.agent.compare import compare_runs
from evals.agent.dataset import Instance, TaskSet, load_task_set
from evals.agent.models import EvalInputs, FailureStage, GradingSpec, TargetResult


def _tasks() -> TaskSet:
    return TaskSet(
        instances=[
            Instance(
                inputs=EvalInputs(
                    instance_id=name,
                    repo="probe/repo",
                    base_commit="a" * 40,
                    problem_statement="Fix addition",
                ),
                outputs=GradingSpec(fail_to_pass=["test_calc.py::test_add"]),
                gold_patch="secret reference patch",
            )
            for name in ("a", "b")
        ]
    )


def _row(
    identity: str,
    stage: FailureStage,
    *,
    steps: int | None = None,
    seconds: float = 1.0,
    tokens: int | None = None,
) -> dict[str, object]:
    return {
        "instance_id": identity,
        "repo": "probe/repo",
        "resolved": stage == FailureStage.NONE,
        "failure_stage": stage,
        "wall_clock_seconds": seconds,
        "steps": steps,
        "input_tokens": tokens,
        "output_tokens": tokens,
        "usage_complete": tokens is not None,
    }


def _experiment(directory: Path, rows: list[dict[str, object]], **overrides: object) -> Path:
    directory.mkdir()
    tasks = _tasks()
    config: dict[str, object] = {
        "run_mode": "agent",
        "contains_model_predictions": True,
        "dataset_sha256": tasks.fingerprint,
        "judge_policy": {"scoring_sha256": "policy"},
        "max_concurrency": 1,
    }
    config.update(overrides)
    (directory / "tasks.json").write_text(tasks.model_dump_json(), encoding="utf-8")
    (directory / "summary.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "completed": True,
                "expected_instances": 2,
                "evaluated_instances": len(rows),
                "config": config,
            }
        ),
        encoding="utf-8",
    )
    (directory / "results.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows), encoding="utf-8"
    )
    return directory


def test_task_identity_covers_grading_but_not_row_order(tmp_path: Path) -> None:
    tasks = _tasks()
    reordered = TaskSet(instances=list(reversed(tasks.instances)))
    assert tasks.fingerprint == reordered.fingerprint
    changed = tasks.model_copy(deep=True)
    changed.instances[0].outputs.pass_to_pass.append("test_calc.py::test_old")
    assert changed.fingerprint != tasks.fingerprint
    path = tmp_path / "tasks.jsonl"
    row = {**tasks.instances[0].inputs.model_dump(), **tasks.instances[0].outputs.model_dump()}
    path.write_text(json.dumps(row) + "\n" + json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        load_task_set(path, limit=1)
    path.write_text(json.dumps(row) + '\n{"repo": "invalid"}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_task_set(path)


async def test_local_run_preserves_execution_failures_and_hides_gold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = AsyncMock(
        return_value=TargetResult(
            instance_id="a",
            failure_stage=FailureStage.AGENT_EXECUTION,
            error="provider unavailable",
        ).model_dump()
    )
    monkeypatch.setattr(run_local, "solve_task", target)
    directory = tmp_path / "run"
    records = await run_local.run_local(_tasks(), directory, mode="agent", config={})
    assert len(records) == 2
    assert all(row["failure_stage"] == FailureStage.AGENT_EXECUTION for row in records)
    for call in target.await_args_list:
        assert set(call.args[0]) == set(EvalInputs.model_fields)
        assert "secret reference patch" not in json.dumps(call.args[0])
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["completed"] is True
    assert summary["metrics"]["resolve_rate"] == 0.0
    assert len(list((directory / "predictions").glob("*.json"))) == 2
    with pytest.raises(FileExistsError):
        await run_local.run_local(_tasks(), directory, mode="agent", config={})


async def test_cancelled_run_saves_incomplete_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = AsyncMock(side_effect=RuntimeError("unexpected runner failure"))
    monkeypatch.setattr(run_local, "solve_task", target)
    directory = tmp_path / "failed"
    with pytest.raises(ExceptionGroup, match="TaskGroup"):
        await run_local.run_local(_tasks(), directory, mode="agent", config={})
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["completed"] is False
    assert summary["expected_instances"] == 2
    assert summary["evaluated_instances"] == 0


def test_comparison_pairs_tasks_and_excludes_missing_usage(tmp_path: Path) -> None:
    baseline = _experiment(
        tmp_path / "baseline",
        [
            _row("a", FailureStage.AGENT_EXECUTION, seconds=10),
            _row("b", FailureStage.NONE, steps=3, seconds=5, tokens=100),
        ],
    )
    candidate = _experiment(
        tmp_path / "candidate",
        [
            _row("b", FailureStage.REGRESSION, steps=5, seconds=9, tokens=80),
            _row("a", FailureStage.NONE, steps=2, seconds=6, tokens=50),
        ],
    )
    result = compare_runs(baseline, candidate)
    assert result["paired_instances"] == 2
    assert result["resolve_rate_delta_percentage_points"] == 0.0
    assert result["improved_instance_ids"] == ["a"]
    assert result["regressed_instance_ids"] == ["b"]
    assert result["observed_step_pairs"] == 1
    assert result["median_paired_steps_delta"] == 2
    assert result["complete_usage_pairs"] == 1
    assert result["median_paired_main_agent_tokens_delta"] == -40
    assert result["median_paired_agent_seconds_delta"] == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "incomplete",
        "dataset",
        "policy",
        "reference",
        "snapshot",
    ],
)
def test_comparison_rejects_unfair_or_incomplete_runs(tmp_path: Path, mutation: str) -> None:
    rows = [_row("a", FailureStage.NONE), _row("b", FailureStage.NO_PATCH)]
    baseline = _experiment(tmp_path / "baseline", rows)
    candidate = _experiment(tmp_path / "candidate", rows)
    summary_path = candidate / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if mutation in {"missing", "duplicate"}:
        changed = rows[:1] if mutation == "missing" else [rows[0], rows[0]]
        (candidate / "results.jsonl").write_text(
            "\n".join(json.dumps(row) for row in changed), encoding="utf-8"
        )
    elif mutation == "incomplete":
        summary["completed"] = False
    elif mutation == "dataset":
        summary["config"]["dataset_sha256"] = "0" * 64
    elif mutation == "policy":
        summary["config"]["judge_policy"] = {"scoring_sha256": "different"}
    elif mutation == "reference":
        summary["config"]["run_mode"] = "reference"
    else:
        tasks = _tasks()
        tasks.instances[0].inputs.problem_statement = "different task"
        (candidate / "tasks.json").write_text(tasks.model_dump_json(), encoding="utf-8")
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(ValueError):
        compare_runs(baseline, candidate)
