"""Deterministic patch grading with explicit execution and correctness failures."""

import asyncio
import logging
import os
import re
import signal
import sys
import tempfile
import time
import tomllib
from collections.abc import Mapping
from pathlib import Path
from statistics import median
from threading import Lock
from typing import TYPE_CHECKING, NotRequired, TypedDict

from langsmith.evaluation import EvaluationResult
from langsmith.evaluation.evaluator import EvaluationResults
from langsmith.schemas import Example, Run
from pydantic import ValidationError

from agent.prompts import render_prompt
from evals.agent.models import FailureStage, GradingSpec, TargetResult

if TYPE_CHECKING:
    from agent.sandboxes.windows_job import WindowsJob

logger = logging.getLogger(__name__)
DEFAULT_TEST_TIMEOUT_SECONDS = 1800
_FENCE_RE = re.compile(r"```(?:diff|patch)?\s*\n(.*?)```", re.DOTALL)
_TEST_LINE_RE = re.compile(r"^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\s+(\S+)", re.MULTILINE)
_SUMMARY_TAIL_CHARS = 4000


class InstanceResult(TypedDict):
    instance_id: str
    repo: str
    resolved: bool
    patch_applied: bool
    fail_to_pass_rate: float
    pass_to_pass_rate: float
    steps: int | None
    wall_clock_seconds: float
    error: str
    failure_stage: NotRequired[FailureStage]
    diagnostic_output: NotRequired[str]
    failed_test_ids: NotRequired[list[str]]
    errored_test_ids: NotRequired[list[str]]
    missing_test_ids: NotRequired[list[str]]
    test_exit_code: NotRequired[int | None]
    timed_out: NotRequired[bool]
    phase_seconds: NotRequired[dict[str, float]]
    thread_id: NotRequired[str]
    input_tokens: NotRequired[int | None]
    output_tokens: NotRequired[int | None]
    usage_complete: NotRequired[bool]
    execution_environment: NotRequired[dict[str, object] | None]
    compaction: NotRequired[dict[str, object] | None]


_records: list[InstanceResult] = []
_records_lock = Lock()


def reset_results() -> None:
    with _records_lock:
        _records.clear()


def get_results() -> list[InstanceResult]:
    with _records_lock:
        return list(_records)


def test_timeout_seconds() -> int:
    raw = os.getenv("AGENT_EVAL_TEST_TIMEOUT_SECONDS", str(DEFAULT_TEST_TIMEOUT_SECONDS))
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("Invalid agent eval test timeout; using default", extra={"configured": raw})
        return DEFAULT_TEST_TIMEOUT_SECONDS


def python_command() -> str:
    return os.getenv("AGENT_EVAL_PYTHON") or sys.executable


def extract_patch(text: str) -> str:
    if not text:
        return ""
    blocks = _FENCE_RE.findall(text)
    if blocks:
        return max(blocks, key=len).strip()
    stripped = text.strip()
    return stripped if stripped.startswith(("diff --git", "--- ", "@@ ")) else ""


def parse_test_outcomes(output: str) -> dict[str, str]:
    return {test_id: outcome for outcome, test_id in _TEST_LINE_RE.findall(output)}


def resolution_from_outcomes(
    outcomes: Mapping[str, str], fail_to_pass: list[str], pass_to_pass: list[str]
) -> tuple[bool, float, float]:
    def rate(test_ids: list[str]) -> float:
        if not test_ids:
            return 1.0
        return sum(outcomes.get(test_id) == "PASSED" for test_id in test_ids) / len(test_ids)

    fail_rate, pass_rate = rate(fail_to_pass), rate(pass_to_pass)
    return bool(fail_to_pass) and fail_rate == 1.0 and pass_rate == 1.0, fail_rate, pass_rate


class _CommandResult(TypedDict):
    returncode: int
    output: str
    timed_out: NotRequired[bool]


async def _communicate(
    process: asyncio.subprocess.Process, timeout: int, job: WindowsJob | None = None
) -> _CommandResult:
    communication = asyncio.create_task(process.communicate())
    finished = False
    try:
        stdout, _ = await asyncio.wait_for(asyncio.shield(communication), timeout=timeout)
        finished = True
    except TimeoutError:
        await _terminate(process, job)
        stdout, _ = await communication
        return {
            "returncode": -1,
            "output": stdout.decode("utf-8", "replace") + f"\nCommand timed out after {timeout}s",
            "timed_out": True,
        }
    except BaseException:
        await _terminate(process, job)
        await communication
        raise
    finally:
        if job is not None:
            await job.aclose(terminate=not finished)
    return {"returncode": process.returncode or 0, "output": stdout.decode("utf-8", "replace")}


async def _terminate(process: asyncio.subprocess.Process, job: WindowsJob | None = None) -> None:
    if os.name == "nt":
        if job is not None:
            await job.aclose()
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                logger.debug("Eval process already exited", extra={"process_id": process.pid})
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            logger.debug("Eval process already exited", extra={"process_id": process.pid})
    await process.wait()


async def _run(command: list[str], cwd: Path, timeout: int) -> _CommandResult:
    process, job = await _spawn(command, cwd)
    return await _communicate(process, timeout, job)


async def _run_shell(
    command: str, cwd: Path, timeout: int, env: dict[str, str] | None = None
) -> _CommandResult:
    process, job = await _spawn(command, cwd, env)
    return await _communicate(process, timeout, job)


async def _spawn(
    command: list[str] | str, cwd: Path, env: dict[str, str] | None = None
) -> tuple[asyncio.subprocess.Process, WindowsJob | None]:
    job: WindowsJob | None = None
    if os.name == "nt":
        from agent.sandboxes.windows_job import WindowsJob

        job = WindowsJob()
    process: asyncio.subprocess.Process | None = None
    try:
        if isinstance(command, str):
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                start_new_session=os.name != "nt",
            )
        else:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                start_new_session=os.name != "nt",
            )
        if job is not None and process.returncode is None:
            await job.attach(process.pid)
    except BaseException:
        if process is not None:
            await _terminate(process, job)
        elif job is not None:
            await job.aclose()
        raise
    return process, job


def _install_env() -> dict[str, str]:
    env = dict(os.environ)
    interpreter = python_command()
    if Path(interpreter).anchor:
        env["PATH"] = str(Path(interpreter).parent) + os.pathsep + env.get("PATH", "")
    return env


_EDITABLE_INSTALL = re.compile(r"-e\s+|--editable[\s=]")


async def _install_repo(package: str, workdir: Path, timeout: int) -> tuple[int, str, bool]:
    """Install the checkout a judge grades.

    An editable install leaves the package importable from the shared judge
    interpreter after the checkout is gone, so the next task would import it
    instead of its own. A test patch failing to apply for the wrong reason is
    invisible in the score, so the editable install is taken back once graded.
    """
    env = _install_env()
    env["PIP_UNINSTALL_NO_INPUT"] = "1"
    installed = await _run_shell(package, workdir, timeout, env=env)
    return installed["returncode"], installed["output"], bool(_EDITABLE_INSTALL.search(package))


def _project_name(workdir: Path) -> str | None:
    """The distribution name a checkout declares, in `pip uninstall` form."""
    pyproject = workdir / "pyproject.toml"
    try:
        metadata = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        logger.warning("Could not read project metadata", extra={"error": str(exc)})
        return None
    name = metadata.get("project", {}).get("name") if isinstance(metadata, dict) else None
    return name if isinstance(name, str) else None


async def _prune_editable_install(workdir: Path, timeout: int) -> None:
    # An exec'd `python` is resolved by the OS, which reads the judge's own
    # environment and not the PATH the install ran with; name the interpreter.
    name = _project_name(workdir)
    if name is None:
        logger.warning("Could not name the package to uninstall")
        return
    pruned = await _run([python_command(), "-m", "pip", "uninstall", "-y", name], workdir, timeout)
    if pruned["returncode"] != 0:
        logger.warning(
            "Could not uninstall the graded package",
            extra={"package": name, "output": pruned["output"][-_SUMMARY_TAIL_CHARS:]},
        )


async def _prepare_checkout(
    repo: str, base_commit: str, workdir: Path, timeout: int, *, clone_url: str | None = None
) -> str | None:
    workdir.mkdir(parents=True, exist_ok=True)
    commands = (
        ["git", "init", "-q"],
        ["git", "remote", "add", "origin", clone_url or f"https://github.com/{repo}.git"],
        ["git", "fetch", "-q", "--depth", "1", "origin", base_commit],
        ["git", "checkout", "-q", "FETCH_HEAD"],
    )
    for command in commands:
        result = await _run(command, workdir, timeout)
        if result["returncode"] != 0:
            return f"{' '.join(command[:2])} failed: {result['output']}"
    return None


async def _apply_patch(patch: str, workdir: Path) -> _CommandResult:
    patch_file = workdir.parent / ".eval-agent.patch"
    patch_file.write_text(patch.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    return await _run(["git", "apply", "--whitespace=nowarn", str(patch_file)], workdir, 300)


def _empty_result(inputs: Mapping[str, object]) -> InstanceResult:
    return {
        "instance_id": str(inputs.get("instance_id", "")),
        "repo": str(inputs.get("repo", "")),
        "resolved": False,
        "patch_applied": False,
        "fail_to_pass_rate": 0.0,
        "pass_to_pass_rate": 0.0,
        "steps": None,
        "wall_clock_seconds": 0.0,
        "error": "",
        "failure_stage": FailureStage.NONE,
        "diagnostic_output": "",
        "failed_test_ids": [],
        "errored_test_ids": [],
        "missing_test_ids": [],
        "test_exit_code": None,
        "timed_out": False,
        "phase_seconds": {},
    }


def _failure(
    base: InstanceResult, stage: FailureStage, output: str, *, timed_out: bool = False
) -> InstanceResult:
    logger.warning(
        "Agent evaluation stage failed",
        extra={"instance_id": base["instance_id"], "failure_stage": stage},
    )
    return {
        **base,
        "failure_stage": stage,
        "error": output[-_SUMMARY_TAIL_CHARS:],
        "diagnostic_output": output,
        "timed_out": timed_out,
    }


async def _evaluate_instance(
    *,
    patch: str,
    inputs: Mapping[str, object],
    outputs: Mapping[str, object],
    workdir: Path,
    timeout: int,
) -> InstanceResult:
    result = _empty_result(inputs)
    phase = FailureStage.INVALID_INSTANCE
    phase_seconds: dict[str, float] = {}
    result["phase_seconds"] = phase_seconds
    editable: list[str] = []
    try:
        grading = GradingSpec.model_validate(outputs)
        if grading.test_command != "pytest":
            return _failure(result, phase, f"Unsupported test runner: {grading.test_command}")
        phase = FailureStage.CHECKOUT
        started = time.monotonic()
        checkout_error = await _prepare_checkout(
            result["repo"],
            str(inputs.get("base_commit", "")),
            workdir,
            timeout,
            clone_url=str(inputs.get("clone_url") or "") or None,
        )
        phase_seconds[phase] = round(time.monotonic() - started, 3)
        if checkout_error:
            return _failure(result, phase, checkout_error)

        phase = FailureStage.INSTALL
        started = time.monotonic()
        for command in grading.install_commands:
            returncode, output, is_editable = await _install_repo(command, workdir, timeout)
            if returncode != 0:
                phase_seconds[phase] = round(time.monotonic() - started, 3)
                return _failure(result, phase, f"install command failed ({command}): {output}")
            if is_editable:
                editable.append(command)
        phase_seconds[phase] = round(time.monotonic() - started, 3)

        for phase, content in (
            (FailureStage.AGENT_PATCH, patch),
            (FailureStage.TEST_PATCH, grading.test_patch),
        ):
            started = time.monotonic()
            if content.strip():
                applied = await _apply_patch(content, workdir)
                if applied["returncode"] != 0:
                    phase_seconds[phase] = round(time.monotonic() - started, 3)
                    return _failure(result, phase, applied["output"])
            if phase == FailureStage.AGENT_PATCH:
                result["patch_applied"] = True
            phase_seconds[phase] = round(time.monotonic() - started, 3)

        phase = FailureStage.TEST_EXECUTION
        test_ids = list(dict.fromkeys(grading.fail_to_pass + grading.pass_to_pass))
        started = time.monotonic()
        pytest_result = await _run(
            [
                python_command(),
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "-rA",
                "--tb=short",
                "-q",
                *test_ids,
            ],
            workdir,
            timeout,
        )
        phase_seconds[phase] = round(time.monotonic() - started, 3)
        result["test_exit_code"] = pytest_result["returncode"]
        result["diagnostic_output"] = pytest_result["output"]
        result["timed_out"] = pytest_result.get("timed_out", False)
        outcomes = parse_test_outcomes(pytest_result["output"])
        resolved, fail_rate, pass_rate = resolution_from_outcomes(
            outcomes, grading.fail_to_pass, grading.pass_to_pass
        )
        missing = [test_id for test_id in test_ids if test_id not in outcomes]
        result.update(
            fail_to_pass_rate=fail_rate,
            pass_to_pass_rate=pass_rate,
            missing_test_ids=missing,
            failed_test_ids=[test_id for test_id in test_ids if outcomes.get(test_id) == "FAILED"],
            errored_test_ids=[test_id for test_id in test_ids if outcomes.get(test_id) == "ERROR"],
        )
        if (
            result["timed_out"]
            or pytest_result["returncode"] not in {0, 1}
            or result["errored_test_ids"]
            or (not outcomes and pytest_result["returncode"] != 0)
            or (resolved and pytest_result["returncode"] != 0)
        ):
            return _failure(result, phase, pytest_result["output"], timed_out=result["timed_out"])
        if missing:
            return _failure(
                result,
                FailureStage.MISSING_OUTCOMES,
                f"tests never reported an outcome: {missing}\n{pytest_result['output']}",
            )
        result["resolved"] = resolved and pytest_result["returncode"] == 0
        if not result["resolved"]:
            result["failure_stage"] = (
                FailureStage.NO_PATCH
                if not patch.strip()
                else FailureStage.REGRESSION
                if pass_rate < 1.0
                else FailureStage.TARGET_TESTS
            )
        return result
    except Exception as exc:
        logger.exception(
            "Agent evaluation judge failed",
            extra={"instance_id": result["instance_id"], "failure_stage": phase},
        )
        return _failure(result, phase, f"{type(exc).__name__}: {exc}")
    finally:
        if editable:
            await _prune_editable_install(workdir, timeout)


async def evaluate_target(
    inputs: Mapping[str, object], outputs: Mapping[str, object], run_outputs: Mapping[str, object]
) -> InstanceResult:
    """Grade a target output, preserving upstream failures without rerunning the judge."""
    try:
        target = TargetResult.model_validate(run_outputs)
    except ValidationError as exc:
        return _failure(_empty_result(inputs), FailureStage.AGENT_EXECUTION, str(exc))
    if target.failure_stage != FailureStage.NONE:
        result = _failure(_empty_result(inputs), target.failure_stage, target.error)
    else:
        with tempfile.TemporaryDirectory(prefix="openswe-agent-eval-") as tmp:
            result = await _evaluate_instance(
                patch=target.patch,
                inputs=inputs,
                outputs=outputs,
                workdir=Path(tmp) / "checkout",
                timeout=test_timeout_seconds(),
            )
    return {
        **result,
        "steps": target.steps,
        "wall_clock_seconds": target.wall_clock_seconds,
        "thread_id": target.thread_id,
        "input_tokens": target.input_tokens,
        "output_tokens": target.output_tokens,
        "usage_complete": target.usage_complete,
        "execution_environment": (
            target.execution_environment.model_dump() if target.execution_environment else None
        ),
        "compaction": target.compaction.model_dump() if target.compaction else None,
    }


async def judge_resolution(run: Run, example: Example | None) -> EvaluationResult:
    if example is None:
        raise ValueError("Agent evaluation requires a dataset example")
    run_outputs = dict(run.outputs or {})
    if run.error:
        run_outputs.update(failure_stage=FailureStage.AGENT_EXECUTION, error=run.error)
    result = await evaluate_target(
        dict(example.inputs or {}), dict(example.outputs or {}), run_outputs
    )
    with _records_lock:
        _records.append(result)
    return EvaluationResult(
        key="resolution",
        score=int(result["resolved"]),
        value=str(result.get("failure_stage", FailureStage.NONE)),
        comment=render_prompt(
            "evals/agent-feedback.md",
            failure_stage=result.get("failure_stage", FailureStage.NONE),
            error=result["error"],
        ),
        evaluator_info={key: value for key, value in result.items() if key != "diagnostic_output"},
    )


def _compaction_value(record: InstanceResult, key: str) -> int:
    compaction = record.get("compaction")
    if not isinstance(compaction, dict):
        return 0
    value = compaction.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def summarize_results(records: list[InstanceResult]) -> dict[str, float]:
    if not records:
        return {}
    count = len(records)
    summary: dict[str, float] = {
        "resolve_rate": sum(r["resolved"] for r in records) / count,
        "patch_applied_rate": sum(r["patch_applied"] for r in records) / count,
        "error_rate": sum(bool(r["error"]) for r in records) / count,
        "mean_fail_to_pass_rate": sum(r["fail_to_pass_rate"] for r in records) / count,
        "mean_pass_to_pass_rate": sum(r["pass_to_pass_rate"] for r in records) / count,
        "median_wall_clock_seconds": float(median(r["wall_clock_seconds"] for r in records)),
        "usage_complete_rate": sum(r.get("usage_complete", False) for r in records) / count,
    }
    known_steps = [steps for r in records if (steps := r["steps"]) is not None]
    if known_steps:
        summary["median_steps"] = float(median(known_steps))
    compaction_counts = [_compaction_value(r, "count") for r in records]
    summary["compaction_rate"] = sum(value > 0 for value in compaction_counts) / count
    summary["mean_compactions"] = sum(compaction_counts) / count
    summarized_tokens = sum(_compaction_value(r, "summarized_tokens") for r in records)
    summary_tokens = sum(_compaction_value(r, "summary_tokens") for r in records)
    summary["mean_compaction_summarized_tokens"] = summarized_tokens / count
    summary["mean_compaction_summary_tokens"] = summary_tokens / count
    if summary_tokens:
        summary["compaction_compression_ratio"] = summarized_tokens / summary_tokens
    overflow_counts = [_compaction_value(r, "overflow_count") for r in records]
    summary["overflow_compaction_rate"] = sum(value > 0 for value in overflow_counts) / count
    summary["mean_overflow_compactions"] = sum(overflow_counts) / count
    for stage in FailureStage:
        if stage != FailureStage.NONE:
            summary[f"failure_rate.{stage}"] = (
                sum(r.get("failure_stage") == stage for r in records) / count
            )
    return summary


def aggregate_resolution(runs: list[Run], examples: list[Example]) -> EvaluationResults:
    del runs, examples
    return {
        "results": [
            EvaluationResult(key=key, score=score)
            for key, score in summarize_results(get_results()).items()
        ]
    }
