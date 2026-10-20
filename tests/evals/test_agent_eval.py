"""Unit tests for the agent eval harness (dataset building and judging)."""

import os
from pathlib import Path

import pytest

from evals.agent.build_dataset import InstanceError, build_example
from evals.agent.judge import (
    _install_env,
    extract_patch,
    parse_test_outcomes,
    resolution_from_outcomes,
)
from evals.agent.judge import (
    test_timeout_seconds as judge_test_timeout_seconds,
)

_INSTANCE = {
    "instance_id": "django__django-11099",
    "repo": "django/django",
    "base_commit": "a" * 40,
    "problem_statement": "QuerySet.values() crashes with a joined field.",
    "patch": "diff --git a/x b/x\n",
    "test_patch": "diff --git a/tests/x b/tests/x\n",
    "fail_to_pass": ["tests/x/test_y.py::test_z"],
    "pass_to_pass": ["tests/x/test_y.py::test_other"],
    "install_commands": ["pip install -e ."],
}


def test_build_example_keeps_grading_data_out_of_inputs() -> None:
    example = build_example(dict(_INSTANCE))

    assert set(example["inputs"]) == {
        "instance_id",
        "repo",
        "base_commit",
        "problem_statement",
    }
    outputs = example["outputs"]
    assert outputs["fail_to_pass"] == ["tests/x/test_y.py::test_z"]
    assert outputs["pass_to_pass"] == ["tests/x/test_y.py::test_other"]
    assert outputs["gold_patch"].startswith("diff --git")
    assert outputs["test_command"] == "pytest"
    assert example["metadata"]["repo"] == "django/django"


def test_build_example_defaults_optional_fields() -> None:
    raw = {
        key: _INSTANCE[key]
        for key in ("instance_id", "repo", "base_commit", "problem_statement", "fail_to_pass")
    }
    example = build_example(raw)

    assert example["outputs"]["pass_to_pass"] == []
    assert example["outputs"]["test_patch"] == ""
    assert example["outputs"]["install_commands"] == []
    assert "clone_url" not in example["inputs"]


def test_build_example_carries_clone_url_into_inputs() -> None:
    example = build_example(
        dict(_INSTANCE, clone_url="https://mirror.example.com/pallets/itsdangerous.git")
    )
    # The agent clones from it, so it must live in inputs, not grading outputs.
    assert example["inputs"]["clone_url"] == "https://mirror.example.com/pallets/itsdangerous.git"


@pytest.mark.parametrize("field", ["instance_id", "repo", "base_commit", "problem_statement"])
def test_build_example_rejects_missing_string_field(field: str) -> None:
    raw = {key: value for key, value in _INSTANCE.items() if key != field}
    with pytest.raises(InstanceError, match=field):
        build_example(raw)


def test_build_example_rejects_empty_fail_to_pass() -> None:
    raw = dict(_INSTANCE, fail_to_pass=[])
    with pytest.raises(InstanceError, match="fail_to_pass"):
        build_example(raw)


def test_build_example_rejects_non_string_test_ids() -> None:
    raw = dict(_INSTANCE, fail_to_pass=["ok", 3])
    with pytest.raises(InstanceError, match="fail_to_pass"):
        build_example(raw)


def test_extract_patch_prefers_the_largest_fenced_block() -> None:
    text = (
        "Here is the change:\n"
        "```diff\n"
        "diff --git a/small b/small\n"
        "```\n"
        "And the real one:\n"
        "```\n"
        "diff --git a/real b/real\n"
        "--- a/real\n"
        "+++ b/real\n"
        "```\n"
    )
    assert extract_patch(text) == "diff --git a/real b/real\n--- a/real\n+++ b/real"


def test_extract_patch_accepts_a_raw_diff() -> None:
    raw = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
    assert extract_patch(raw) == raw.strip()


def test_extract_patch_rejects_prose() -> None:
    assert extract_patch("I looked around but the issue is in another repo.") == ""
    assert extract_patch("") == ""


def test_parse_test_outcomes_reads_the_short_summary() -> None:
    output = (
        "........\n"
        "========= short test summary info =========\n"
        "PASSED tests/x/test_y.py::test_z\n"
        "FAILED tests/x/test_y.py::test_other\n"
        "ERROR tests/x/test_y.py::test_broken\n"
    )
    assert parse_test_outcomes(output) == {
        "tests/x/test_y.py::test_z": "PASSED",
        "tests/x/test_y.py::test_other": "FAILED",
        "tests/x/test_y.py::test_broken": "ERROR",
    }


def test_resolution_requires_every_fail_to_pass_to_pass() -> None:
    outcomes = {
        "tests/x.py::test_a": "PASSED",
        "tests/x.py::test_b": "FAILED",
        "tests/x.py::test_regression": "PASSED",
    }
    resolved, fail_rate, pass_rate = resolution_from_outcomes(
        outcomes, ["tests/x.py::test_a", "tests/x.py::test_b"], ["tests/x.py::test_regression"]
    )
    assert resolved is False
    assert fail_rate == 0.5
    assert pass_rate == 1.0


def test_resolution_detects_a_pass_to_pass_regression() -> None:
    outcomes = {"tests/x.py::test_a": "PASSED", "tests/x.py::test_old": "FAILED"}
    resolved, fail_rate, pass_rate = resolution_from_outcomes(
        outcomes, ["tests/x.py::test_a"], ["tests/x.py::test_old"]
    )
    assert resolved is False
    assert fail_rate == 1.0
    assert pass_rate == 0.0


def test_resolution_with_no_regressions_is_resolved() -> None:
    outcomes = {"tests/x.py::test_a": "PASSED"}
    resolved, fail_rate, pass_rate = resolution_from_outcomes(outcomes, ["tests/x.py::test_a"], [])
    assert resolved is True
    assert fail_rate == 1.0
    assert pass_rate == 1.0


def test_test_timeout_seconds_defaults_and_env(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_EVAL_TEST_TIMEOUT_SECONDS", raising=False)
    assert judge_test_timeout_seconds() == 1800
    monkeypatch.setenv("AGENT_EVAL_TEST_TIMEOUT_SECONDS", "600")
    assert judge_test_timeout_seconds() == 600
    monkeypatch.setenv("AGENT_EVAL_TEST_TIMEOUT_SECONDS", "not-a-number")
    assert judge_test_timeout_seconds() == 1800


def test_install_env_puts_the_pinned_interpreters_bin_dir_on_path(monkeypatch) -> None:
    interpreter = "/opt/eval-venv/bin/python"
    monkeypatch.setenv("AGENT_EVAL_PYTHON", interpreter)
    monkeypatch.setenv("PATH", "/usr/bin")
    env = _install_env()
    # Compare through Path so the assertion holds on POSIX and Windows runners.
    assert env["PATH"].startswith(str(Path(interpreter).parent) + os.pathsep)
    assert env["PATH"].endswith("/usr/bin")


def test_install_env_leaves_path_alone_for_bare_interpreters(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EVAL_PYTHON", "python")
    monkeypatch.setenv("PATH", "/usr/bin")
    assert _install_env()["PATH"] == "/usr/bin"
