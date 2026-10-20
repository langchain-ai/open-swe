"""Deterministic test-based evaluator for the agent eval.

No LLM judge: an instance is resolved when every ``fail_to_pass`` test passes
and every ``pass_to_pass`` test still passes after applying the agent's patch —
and the benchmark's ``test_patch`` — to a clean checkout at ``base_commit``.
That is the same definition SWE-bench uses, so numbers are comparable to
published leaderboards.

The judge shells out to ``git`` and ``pytest`` in a temp directory. It assumes a
POSIX runner with network access to github.com; CI should run it on a durable
runner, the same way the reviewer eval does.
"""

import asyncio
import logging
import os
import re
import tempfile
from pathlib import Path
from threading import Lock
from typing import Any, TypedDict

from langsmith.schemas import Example, Run

logger = logging.getLogger(__name__)

DEFAULT_TEST_TIMEOUT_SECONDS = 1800
_FENCE_RE = re.compile(r"```(?:diff|patch)?\s*\n(.*?)```", re.DOTALL)
_TEST_LINE_RE = re.compile(r"^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\s+(\S+)", re.MULTILINE)
_SUMMARY_TAIL_CHARS = 4000

_records: list[dict[str, Any]] = []
_records_lock = Lock()


class InstanceResult(TypedDict):
    instance_id: str
    repo: str
    resolved: bool
    patch_applied: bool
    fail_to_pass_rate: float
    pass_to_pass_rate: float
    steps: int
    wall_clock_seconds: float
    error: str


def test_timeout_seconds() -> int:
    """Per-command timeout the runner configured, in seconds."""
    raw = os.getenv("AGENT_EVAL_TEST_TIMEOUT_SECONDS", str(DEFAULT_TEST_TIMEOUT_SECONDS))
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_TEST_TIMEOUT_SECONDS


def python_command() -> str:
    """Interpreter the judge uses for pytest.

    The judge spawns the repo's tests in a fresh checkout, so it needs an
    interpreter with pytest installed. Pin it when bare ``python`` on PATH is
    not the one you mean (Windows Store stubs, pyenv shims, a specific venv).
    """
    return os.getenv("AGENT_EVAL_PYTHON", "python")


def extract_patch(text: str) -> str:
    """Pull a raw diff out of a model message.

    Prefers the largest fenced block (models often wrap diffs in ```diff);
    falls back to the whole message when it already looks like a diff. Prose
    without a diff yields ``""`` — a no-change run, which scores zero.
    """
    if not text:
        return ""
    blocks = _FENCE_RE.findall(text)
    if blocks:
        return max(blocks, key=len).strip()
    stripped = text.strip()
    if stripped.startswith(("diff --git", "--- ", "@@ ")):
        return stripped
    return ""


def parse_test_outcomes(output: str) -> dict[str, str]:
    """Per-test outcomes from ``pytest -rA`` short-summary lines."""
    return {test_id: outcome for outcome, test_id in _TEST_LINE_RE.findall(output)}


def resolution_from_outcomes(
    outcomes: dict[str, str],
    fail_to_pass: list[str],
    pass_to_pass: list[str],
) -> tuple[bool, float, float]:
    """Resolved, fail_to_pass rate, pass_to_pass rate for one instance."""

    def rate(test_ids: list[str]) -> float:
        if not test_ids:
            return 1.0
        passed = sum(1 for test_id in test_ids if outcomes.get(test_id) == "PASSED")
        return passed / len(test_ids)

    fail_to_pass_rate = rate(fail_to_pass)
    pass_to_pass_rate = rate(pass_to_pass)
    resolved = fail_to_pass_rate == 1.0 and pass_to_pass_rate == 1.0
    return resolved, fail_to_pass_rate, pass_to_pass_rate


class _CommandResult(TypedDict):
    returncode: int
    output: str


async def _run(command: list[str], cwd: Path, timeout: int) -> _CommandResult:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return {"returncode": -1, "output": f"command timed out after {timeout}s: {command}"}
    return {"returncode": process.returncode or 0, "output": stdout.decode("utf-8", "replace")}


async def _run_shell(
    command: str, cwd: Path, timeout: int, env: dict[str, str] | None = None
) -> _CommandResult:
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return {"returncode": -1, "output": f"command timed out after {timeout}s: {command}"}
    return {"returncode": process.returncode or 0, "output": stdout.decode("utf-8", "replace")}


def _install_env() -> dict[str, str]:
    """Environment for a repo's install commands.

    When ``AGENT_EVAL_PYTHON`` names an interpreter by absolute path (a venv),
    that interpreter's bin directory goes on PATH so a bare ``pip install``
    resolves to the same environment the judge runs pytest in. Without this,
    ``pip`` is frequently not on PATH at all and every install fails.
    """
    env = dict(os.environ)
    interpreter = python_command()
    # Any anchored path has a directory worth putting on PATH; a bare name
    # ("python") resolves through PATH as usual and has nothing to add.
    if Path(interpreter).anchor:
        env["PATH"] = str(Path(interpreter).parent) + os.pathsep + env.get("PATH", "")
    return env


async def _prepare_checkout(
    repo: str,
    base_commit: str,
    workdir: Path,
    timeout: int,
    *,
    clone_url: str | None = None,
) -> str | None:
    """Clone ``repo`` at ``base_commit`` into ``workdir``. Returns an error string."""
    workdir.mkdir(parents=True, exist_ok=True)
    remote_url = clone_url or f"https://github.com/{repo}.git"
    init = await _run(["git", "init", "-q"], workdir, 300)
    if init["returncode"] != 0:
        return f"git init failed: {init['output'][-_SUMMARY_TAIL_CHARS:]}"
    remote = await _run(["git", "remote", "add", "origin", remote_url], workdir, 300)
    if remote["returncode"] != 0:
        return f"git remote add failed: {remote['output'][-_SUMMARY_TAIL_CHARS:]}"
    fetch = await _run(
        ["git", "fetch", "-q", "--depth", "1", "origin", base_commit], workdir, timeout
    )
    if fetch["returncode"] != 0:
        return f"git fetch {base_commit} failed: {fetch['output'][-_SUMMARY_TAIL_CHARS:]}"
    checkout = await _run(["git", "checkout", "-q", "FETCH_HEAD"], workdir, 300)
    if checkout["returncode"] != 0:
        return f"git checkout failed: {checkout['output'][-_SUMMARY_TAIL_CHARS:]}"
    return None


async def _apply_patch(patch: str, workdir: Path) -> _CommandResult:
    # Written outside the checkout so no test suite ever sees the stray file.
    patch_file = workdir.parent / ".eval-agent.patch"
    patch_file.write_text(patch)
    return await _run(["git", "apply", "--whitespace=nowarn", str(patch_file)], workdir, 300)


async def _evaluate_instance(
    *,
    patch: str,
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    workdir: Path,
    timeout: int,
) -> InstanceResult:
    repo = str(inputs.get("repo", ""))
    base_commit = str(inputs.get("base_commit", ""))
    instance_id = str(inputs.get("instance_id", ""))
    # Instances may name a mirror or self-hosted remote; github.com is the default.
    clone_url = str(inputs.get("clone_url") or "") or None
    fail_to_pass = [str(test_id) for test_id in outputs.get("fail_to_pass") or []]
    pass_to_pass = [str(test_id) for test_id in outputs.get("pass_to_pass") or []]
    test_patch = str(outputs.get("test_patch") or "")
    install_commands = [str(command) for command in outputs.get("install_commands") or []]
    test_command = str(outputs.get("test_command") or "pytest")
    if test_command != "pytest":
        logger.warning(
            "Instance %s requests test_command %r but only pytest is wired up; "
            "running pytest instead",
            instance_id,
            test_command,
        )

    base = InstanceResult(
        instance_id=instance_id,
        repo=repo,
        resolved=False,
        patch_applied=False,
        fail_to_pass_rate=0.0,
        pass_to_pass_rate=0.0,
        steps=0,
        wall_clock_seconds=0.0,
        error="",
    )

    checkout_error = await _prepare_checkout(
        repo, base_commit, workdir, timeout, clone_url=clone_url
    )
    if checkout_error is not None:
        logger.warning("Checkout failed for %s: %s", instance_id, checkout_error)
        return {**base, "error": checkout_error}

    for command in install_commands:
        installed = await _run_shell(command, workdir, timeout, env=_install_env())
        if installed["returncode"] != 0:
            message = (
                f"install command failed ({command}): {installed['output'][-_SUMMARY_TAIL_CHARS:]}"
            )
            logger.warning("Install failed for %s: %s", instance_id, message)
            return {**base, "error": message}

    patch_applied = True
    if patch.strip():
        applied = await _apply_patch(patch, workdir)
        if applied["returncode"] != 0:
            patch_applied = False
            logger.info(
                "Agent patch did not apply for %s: %s",
                instance_id,
                applied["output"][-_SUMMARY_TAIL_CHARS:],
            )
    if test_patch.strip():
        applied = await _apply_patch(test_patch, workdir)
        if applied["returncode"] != 0:
            message = f"test patch did not apply: {applied['output'][-_SUMMARY_TAIL_CHARS:]}"
            logger.warning("Test patch failed for %s: %s", instance_id, message)
            return {**base, "patch_applied": patch_applied, "error": message}
    test_ids = fail_to_pass + pass_to_pass
    pytest_result = await _run(
        [
            python_command(),
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-rA",
            "--tb=no",
            "-q",
            *test_ids,
        ],
        workdir,
        timeout,
    )
    outcomes = parse_test_outcomes(pytest_result["output"])
    resolved, fail_to_pass_rate, pass_to_pass_rate = resolution_from_outcomes(
        outcomes, fail_to_pass, pass_to_pass
    )
    missing = [test_id for test_id in test_ids if test_id not in outcomes]
    error = ""
    if missing:
        error = f"tests never reported an outcome: {missing[:5]}"
        logger.warning("Missing outcomes for %s: %s", instance_id, missing[:5])
    return {
        **base,
        "resolved": resolved,
        "patch_applied": patch_applied,
        "fail_to_pass_rate": fail_to_pass_rate,
        "pass_to_pass_rate": pass_to_pass_rate,
        "error": error,
    }


async def judge_resolution(run: Run, example: Example) -> dict[str, Any]:
    """Apply the agent's patch and run the benchmark tests."""
    inputs = dict(example.inputs or {})
    outputs = dict(example.outputs or {})
    run_outputs = dict(run.outputs or {})
    patch = str(run_outputs.get("patch") or "")
    timeout = test_timeout_seconds()

    with tempfile.TemporaryDirectory(prefix="openswe-agent-eval-") as tmp:
        result = await _evaluate_instance(
            patch=extract_patch(patch),
            inputs=inputs,
            outputs=outputs,
            workdir=Path(tmp) / "checkout",
            timeout=timeout,
        )

    result = {
        **result,
        "steps": int(run_outputs.get("steps") or 0),
        "wall_clock_seconds": float(run_outputs.get("wall_clock_seconds") or 0.0),
    }
    with _records_lock:
        _records.append(result)
    return {
        "key": "resolution",
        "score": 1 if result["resolved"] else 0,
        **result,
    }


def aggregate_resolution(runs: list[Run], examples: list[Example]) -> dict[str, Any]:
    """Summary evaluator: resolve rate and cost proxies across the experiment.

    Reads the per-example results that ``judge_resolution`` stashed in the
    process-local list. Falls back to an empty result set if the list is empty
    (e.g. the summary evaluator ran in a different process).
    """
    del runs, examples
    with _records_lock:
        records = list(_records)
        _records.clear()
    if not records:
        return {"results": []}

    scored = len(records)
    resolved = sum(1 for record in records if record["resolved"])
    patch_applied = sum(1 for record in records if record["patch_applied"])
    errored = sum(1 for record in records if record["error"])
    steps = sorted(record["steps"] for record in records)
    wall_clock = sorted(record["wall_clock_seconds"] for record in records)

    def _mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    def _median(values: list[float]) -> float:
        if not values:
            return 0.0
        middle = len(values) // 2
        if len(values) % 2:
            return values[middle]
        return (values[middle - 1] + values[middle]) / 2

    return {
        "results": [
            {"key": "resolve_rate", "score": resolved / scored},
            {"key": "patch_applied_rate", "score": patch_applied / scored},
            {"key": "error_rate", "score": errored / scored},
            {
                "key": "mean_fail_to_pass_rate",
                "score": _mean([r["fail_to_pass_rate"] for r in records]),
            },
            {
                "key": "mean_pass_to_pass_rate",
                "score": _mean([r["pass_to_pass_rate"] for r in records]),
            },
            {"key": "median_steps", "score": _median(steps)},
            {"key": "median_wall_clock_seconds", "score": _median(wall_clock)},
        ]
    }
