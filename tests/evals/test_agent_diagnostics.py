"""Judge failures retain their stage and cannot produce false successful scores."""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langsmith.evaluation import run_evaluator
from langsmith.schemas import Example, Run

from evals.agent import judge
from evals.agent.judge import InstanceResult
from evals.agent.models import FailureStage
from evals.agent.report import write_report


async def test_cancelling_judge_terminates_test_descendants(tmp_path: Path) -> None:
    (tmp_path / "child.py").write_text(
        "from pathlib import Path\nimport time\nPath('ready').touch()\n"
        "time.sleep(1.5)\nPath('escaped').touch()\n",
        encoding="utf-8",
    )
    (tmp_path / "parent.py").write_text(
        "import subprocess,sys\nsubprocess.run([sys.executable,'child.py'],check=True)\n",
        encoding="utf-8",
    )
    running = asyncio.create_task(judge._run([sys.executable, "parent.py"], tmp_path, timeout=10))
    async with asyncio.timeout(10):
        while not (tmp_path / "ready").exists():
            await asyncio.sleep(0.02)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    await asyncio.sleep(1.7)
    assert not (tmp_path / "escaped").exists()


@pytest.mark.parametrize(
    ("returncode", "output", "timed_out", "stage"),
    [
        (0, "PASSED test.py::target\nPASSED test.py::old", False, FailureStage.NONE),
        (2, "PASSED test.py::target\nPASSED test.py::old", False, FailureStage.TEST_EXECUTION),
        (1, "PASSED test.py::target\nPASSED test.py::old", False, FailureStage.TEST_EXECUTION),
        (1, "ERROR test.py::target\nPASSED test.py::old", False, FailureStage.TEST_EXECUTION),
        (-1, "partial output", True, FailureStage.TEST_EXECUTION),
        (1, "PASSED test.py::old", False, FailureStage.MISSING_OUTCOMES),
        (1, "FAILED test.py::target\nPASSED test.py::old", False, FailureStage.TARGET_TESTS),
    ],
)
async def test_exit_status_and_missing_outcomes_affect_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    returncode: int,
    output: str,
    timed_out: bool,
    stage: FailureStage,
) -> None:
    monkeypatch.setattr(judge, "_prepare_checkout", AsyncMock(return_value=None))
    monkeypatch.setattr(
        judge, "_apply_patch", AsyncMock(return_value={"returncode": 0, "output": ""})
    )
    monkeypatch.setattr(
        judge,
        "_run",
        AsyncMock(
            return_value={
                "returncode": returncode,
                "output": output,
                "timed_out": timed_out,
            }
        ),
    )
    result = await judge._evaluate_instance(
        patch="nonempty patch",
        inputs={"instance_id": "probe", "repo": "probe/repo"},
        outputs={"fail_to_pass": ["test.py::target"], "pass_to_pass": ["test.py::old"]},
        workdir=tmp_path,
        timeout=5,
    )
    assert result["resolved"] is (stage == FailureStage.NONE)
    assert result["failure_stage"] == stage
    assert result["test_exit_code"] == returncode
    assert result["timed_out"] == timed_out
    assert output in result["diagnostic_output"]


async def test_install_error_preserves_complete_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = "important first line\n" + "x" * 5000 + "\nlast line"
    monkeypatch.setattr(judge, "_prepare_checkout", AsyncMock(return_value=None))
    monkeypatch.setattr(
        judge, "_run_shell", AsyncMock(return_value={"returncode": 1, "output": output})
    )
    result = await judge._evaluate_instance(
        patch="",
        inputs={"instance_id": "probe", "repo": "probe/repo"},
        outputs={"fail_to_pass": ["test.py::target"], "install_commands": ["install"]},
        workdir=tmp_path,
        timeout=5,
    )
    assert result["failure_stage"] == "install"
    assert output in result["diagnostic_output"]
    assert len(result["error"]) <= 4000


async def test_upstream_failure_does_not_start_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    checkout = AsyncMock()
    monkeypatch.setattr(judge, "_prepare_checkout", checkout)
    result = await judge.evaluate_target(
        {"instance_id": "probe", "repo": "probe/repo"},
        {},
        {"failure_stage": "patch_collection", "error": "download unavailable", "steps": 7},
    )
    assert result["failure_stage"] == "patch_collection"
    assert result["steps"] == 7
    assert result["resolved"] is False
    checkout.assert_not_awaited()


async def test_unsupported_runner_is_not_silently_replaced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    checkout = AsyncMock()
    monkeypatch.setattr(judge, "_prepare_checkout", checkout)
    result = await judge._evaluate_instance(
        patch="",
        inputs={"instance_id": "probe", "repo": "probe/repo"},
        outputs={"fail_to_pass": ["test.js"], "test_command": "jest"},
        workdir=tmp_path,
        timeout=5,
    )
    assert result["failure_stage"] == "invalid_instance"
    checkout.assert_not_awaited()


async def test_timeout_retains_output_emitted_before_termination(tmp_path: Path) -> None:
    result = await judge._run(
        [
            sys.executable,
            "-u",
            "-c",
            "import time; print('failure evidence', flush=True); time.sleep(60)",
        ],
        tmp_path,
        2,
    )
    assert result["timed_out"] is True
    assert "failure evidence" in result["output"]


async def test_judge_uses_its_own_environment_when_path_has_no_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_EVAL_PYTHON", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    result = await judge._run(
        [judge.python_command(), "-c", "import pytest; print('pytest available')"], tmp_path, 10
    )
    assert result["returncode"] == 0
    assert "pytest available" in result["output"]


@pytest.mark.parametrize(("completed", "expected_instances"), [(False, 2), (True, 3)])
async def test_report_keeps_failures_in_denominator_and_marks_incomplete(
    tmp_path: Path, completed: bool, expected_instances: int
) -> None:
    records: list[InstanceResult] = [
        {
            "instance_id": "success",
            "repo": "probe/repo",
            "resolved": True,
            "patch_applied": True,
            "fail_to_pass_rate": 1.0,
            "pass_to_pass_rate": 1.0,
            "steps": 3,
            "wall_clock_seconds": 4.0,
            "error": "",
            "failure_stage": FailureStage.NONE,
            "usage_complete": True,
        },
        {
            "instance_id": "failure",
            "repo": "probe/repo",
            "resolved": False,
            "patch_applied": False,
            "fail_to_pass_rate": 0.0,
            "pass_to_pass_rate": 0.0,
            "steps": 0,
            "wall_clock_seconds": 1.0,
            "error": "checkout failed",
            "failure_stage": FailureStage.CHECKOUT,
            "diagnostic_output": "complete evidence",
        },
    ]
    await write_report(
        tmp_path,
        records,
        config={"model_id": "test-model"},
        completed=completed,
        expected_instances=expected_instances,
    )
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["completed"] is False
    assert summary["metrics"]["resolve_rate"] == 0.5
    assert summary["metrics"]["usage_complete_rate"] == 0.5
    assert summary["failure_counts"] == {"checkout": 1}
    rows = [json.loads(line) for line in (tmp_path / "results.jsonl").read_text().splitlines()]
    assert rows[1]["diagnostic_output"] == "complete evidence"
    assert (tmp_path / "report.md").is_file()


async def test_langsmith_adapter_accepts_diagnosis_and_attaches_it_to_feedback() -> None:
    run = Run(
        id=uuid4(),
        name="probe",
        run_type="chain",
        start_time=datetime.now(UTC),
        inputs={},
        outputs={"failure_stage": "agent_execution", "error": "provider failed"},
    )
    example = Example(
        id=uuid4(),
        dataset_id=uuid4(),
        created_at=datetime.now(UTC),
        inputs={"instance_id": "probe", "repo": "probe/repo"},
        outputs={},
    )
    feedback = await run_evaluator(judge.judge_resolution).aevaluate_run(run, example)
    assert feedback.score == 0
    assert feedback.value == "agent_execution"
    assert feedback.evaluator_info["failure_stage"] == "agent_execution"
    assert "provider failed" in feedback.comment
