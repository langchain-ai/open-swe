"""End-to-end judge test: real git checkout, real patch application, real pytest.

Builds a throwaway repository with a known bug and drives the same
``_evaluate_instance`` path the LangSmith evaluator uses. No network: the
"origin" is a local bare repo and the judge fetches the base commit by SHA.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.agent.judge import _evaluate_instance

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is unavailable on this host"
)

_BUGGY_CALC = "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"
_FIXED_CALC = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
_REGRESSION_CALC = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a + b\n"
_TEST_BASE = "from calc import add, sub\n\n\ndef test_sub():\n    assert sub(3, 1) == 2\n"
_TEST_WITH_ADD = _TEST_BASE + "\n\ndef test_add():\n    assert add(1, 2) == 3\n"

_NON_APPLYING_PATCH = (
    "diff --git a/calc.py b/calc.py\n"
    "--- a/calc.py\n"
    "+++ b/calc.py\n"
    "@@ -1,2 +1,2 @@\n"
    "-def nonexistent(a, b):\n"
    "+def add(a, b):\n"
    "     return a - b\n"
)


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout


def _diff_for(change: str, repo: Path) -> str:
    """Patch that turns the committed calc.py into ``change``."""
    (repo / "calc.py").write_text(change, newline="\n")
    patch = _git("diff", cwd=repo)
    _git("checkout", "--", "calc.py", cwd=repo)
    return patch


@pytest.fixture(scope="module")
def probe_repo(tmp_path_factory: pytest.TempPathFactory) -> SimpleNamespace:
    root = tmp_path_factory.mktemp("probe")
    repo = root / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("config", "core.autocrlf", "false", cwd=repo)
    (repo / "calc.py").write_text(_BUGGY_CALC, newline="\n")
    (repo / "test_calc.py").write_text(_TEST_BASE, newline="\n")
    _git("add", ".", cwd=repo)
    _git(
        "-c",
        "user.email=probe@example.com",
        "-c",
        "user.name=probe",
        "commit",
        "-q",
        "-m",
        "base",
        cwd=repo,
    )
    base_commit = _git("rev-parse", "HEAD", cwd=repo).strip()

    gold_patch = _diff_for(_FIXED_CALC, repo)
    regression_patch = _diff_for(_REGRESSION_CALC, repo)
    (repo / "test_calc.py").write_text(_TEST_WITH_ADD, newline="\n")
    test_patch = _git("diff", cwd=repo)
    _git("checkout", "--", "test_calc.py", cwd=repo)

    origin = root / "origin.git"
    _git("clone", "-q", "--bare", str(repo), str(origin), cwd=root)

    return SimpleNamespace(
        base_commit=base_commit,
        clone_url=origin.as_uri(),
        gold_patch=gold_patch,
        regression_patch=regression_patch,
        test_patch=test_patch,
    )


async def _score(
    probe_repo: SimpleNamespace,
    patch: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> dict:
    monkeypatch.setenv("AGENT_EVAL_PYTHON", sys.executable)
    return await _evaluate_instance(
        patch=patch,
        inputs={
            "instance_id": "probe__repo-1",
            "repo": "probe/repo",
            "base_commit": probe_repo.base_commit,
            "clone_url": probe_repo.clone_url,
        },
        outputs={
            "fail_to_pass": ["test_calc.py::test_add"],
            "pass_to_pass": ["test_calc.py::test_sub"],
            "test_patch": probe_repo.test_patch,
            "install_commands": [],
        },
        workdir=tmp_path / "checkout",
        timeout=600,
    )


async def test_gold_patch_resolves(probe_repo, monkeypatch, tmp_path) -> None:
    result = await _score(probe_repo, probe_repo.gold_patch, monkeypatch, tmp_path)

    assert result["error"] == ""
    assert result["patch_applied"] is True
    assert result["resolved"] is True
    assert result["fail_to_pass_rate"] == 1.0
    assert result["pass_to_pass_rate"] == 1.0
    assert result["failure_stage"] == "none"


async def test_empty_patch_fails_the_target_tests(probe_repo, monkeypatch, tmp_path) -> None:
    result = await _score(probe_repo, "", monkeypatch, tmp_path)

    assert result["error"] == ""
    assert result["patch_applied"] is True  # nothing to apply is not a failure
    assert result["resolved"] is False
    assert result["fail_to_pass_rate"] == 0.0
    assert result["pass_to_pass_rate"] == 1.0
    assert result["failure_stage"] == "no_patch"


async def test_non_applying_patch_is_flagged(probe_repo, monkeypatch, tmp_path) -> None:
    result = await _score(probe_repo, _NON_APPLYING_PATCH, monkeypatch, tmp_path)

    assert result["patch_applied"] is False
    assert result["resolved"] is False
    assert result["failure_stage"] == "agent_patch"


async def test_pass_to_pass_regression_blocks_resolution(probe_repo, monkeypatch, tmp_path) -> None:
    result = await _score(probe_repo, probe_repo.regression_patch, monkeypatch, tmp_path)

    assert result["patch_applied"] is True
    assert result["fail_to_pass_rate"] == 1.0
    assert result["pass_to_pass_rate"] == 0.0
    assert result["resolved"] is False
    assert result["failure_stage"] == "regression"
    assert result["failed_test_ids"] == ["test_calc.py::test_sub"]


async def test_unreachable_base_commit_is_an_error_not_a_zero(
    probe_repo, monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("AGENT_EVAL_PYTHON", sys.executable)
    with tempfile.TemporaryDirectory() as tmp:
        result = await _evaluate_instance(
            patch=probe_repo.gold_patch,
            inputs={
                "instance_id": "probe__repo-2",
                "repo": "probe/repo",
                "base_commit": "0" * 40,
                "clone_url": probe_repo.clone_url,
            },
            outputs={"fail_to_pass": ["test_calc.py::test_add"], "pass_to_pass": []},
            workdir=Path(tmp) / "checkout",
            timeout=120,
        )
    assert result["resolved"] is False
    assert "git fetch" in result["error"]
    assert result["failure_stage"] == "checkout"
