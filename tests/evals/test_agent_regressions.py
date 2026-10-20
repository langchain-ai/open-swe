"""Frozen failures can be regraded without another model invocation."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from evals.agent import regressions
from evals.agent.dataset import Instance, TaskSet
from evals.agent.judge import InstanceResult
from evals.agent.models import EvalInputs, FailureStage, GradingSpec, TargetResult


def _failure_run(root: Path) -> Path:
    root.mkdir()
    task = Instance(
        inputs=EvalInputs(
            instance_id="failure",
            repo="probe/repo",
            base_commit="a" * 40,
            problem_statement="Fix addition",
        ),
        outputs=GradingSpec(fail_to_pass=["test.py::test_add"]),
    )
    (root / "tasks.json").write_text(TaskSet(instances=[task]).model_dump_json(), encoding="utf-8")
    (root / "predictions").mkdir()
    prediction = TargetResult(
        instance_id="failure",
        repo="probe/repo",
        failure_stage=FailureStage.AGENT_EXECUTION,
        error="provider unavailable",
    )
    (root / "predictions" / "000000.json").write_text(
        prediction.model_dump_json(), encoding="utf-8"
    )
    (root / "results.jsonl").write_text(
        json.dumps(
            {"instance_id": "failure", "resolved": False, "failure_stage": "agent_execution"}
        ),
        encoding="utf-8",
    )
    return root


async def test_freeze_and_replay_preserve_failure_and_write_evidence(tmp_path: Path) -> None:
    root = _failure_run(tmp_path / "source")
    suite = regressions.freeze_cases(root, ["failure"], tmp_path / "cases.json")
    output = tmp_path / "replayed"
    assert await regressions.replay_cases(suite, output) is True
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["config"]["contains_model_predictions"] is False
    assert summary["completed"] is True
    records = (output / "results.jsonl").read_text(encoding="utf-8")
    assert "provider unavailable" in records
    with pytest.raises(ValueError, match="missing"):
        regressions.freeze_cases(root, ["unknown"], tmp_path / "unknown.json")
    with pytest.raises(FileExistsError):
        regressions.freeze_cases(root, ["failure"], tmp_path / "cases.json")


async def test_replay_reports_changed_failure_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _failure_run(tmp_path / "source")
    suite = regressions.freeze_cases(root, ["failure"], tmp_path / "cases.json")
    changed: InstanceResult = {
        "instance_id": "failure",
        "repo": "probe/repo",
        "resolved": False,
        "failure_stage": FailureStage.CHECKOUT,
        "patch_applied": False,
        "fail_to_pass_rate": 0,
        "pass_to_pass_rate": 0,
        "steps": None,
        "wall_clock_seconds": 0,
        "error": "checkout failed",
    }
    monkeypatch.setattr(regressions, "evaluate_target", AsyncMock(return_value=changed))
    output = tmp_path / "replayed"
    assert await regressions.replay_cases(suite, output) is False
    expectations = json.loads((output / "expectations.json").read_text(encoding="utf-8"))
    assert expectations["differences"][0]["actual"]["failure_stage"] == "checkout"
