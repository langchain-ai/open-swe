"""Config resolution and target-helper tests for the agent eval runner."""

import pytest

from evals.agent import run_eval, target


def test_coerce_config_parses_ints_and_skips_blanks() -> None:
    config = run_eval._coerce_config(
        {
            "max_concurrency": "7",
            "test_timeout_seconds": "600",
            "model_id": "",
            "assistant_id": None,
        }
    )
    assert config["max_concurrency"] == 7
    assert config["test_timeout_seconds"] == 600
    assert "model_id" not in config
    assert "assistant_id" not in config


def test_coerce_config_ignores_unparseable_ints() -> None:
    assert "max_concurrency" not in run_eval._coerce_config({"max_concurrency": "many"})


def test_resolve_config_precedence_is_cli_over_env_over_file(monkeypatch, tmp_path) -> None:
    toml = tmp_path / "config.toml"
    toml.write_text('dataset_name = "from-file"\nmax_concurrency = 2\n')
    monkeypatch.setattr(run_eval, "CONFIG_PATH", toml)
    monkeypatch.setenv("AGENT_EVAL_DATASET_NAME", "from-env")

    resolved = run_eval._resolve_config({"dataset_name": "from-cli", "max_concurrency": 9})

    assert resolved["dataset_name"] == "from-cli"
    assert resolved["max_concurrency"] == 9
    # Unset keys fall back to the file, then to the defaults.
    assert resolved["langsmith_project"] == "open-swe-evals"


def test_apply_config_to_env_exports_runner_knobs(monkeypatch) -> None:
    for key in (
        "AGENT_EVAL_DATASET_NAME",
        "AGENT_EVAL_MODEL_ID",
        "AGENT_EVAL_TEST_TIMEOUT_SECONDS",
        "LANGSMITH_PROJECT",
    ):
        monkeypatch.delenv(key, raising=False)
    run_eval._apply_config_to_env(
        {"dataset_name": "ds", "model_id": "m", "test_timeout_seconds": 600}
    )
    import os

    assert os.environ["AGENT_EVAL_DATASET_NAME"] == "ds"
    assert os.environ["AGENT_EVAL_MODEL_ID"] == "m"
    assert os.environ["AGENT_EVAL_TEST_TIMEOUT_SECONDS"] == "600"
    assert os.environ["LANGSMITH_PROJECT"] == "open-swe-evals"


def test_build_configurable_marks_the_run_as_eval(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_EVAL_MODEL_ID", raising=False)
    monkeypatch.delenv("AGENT_EVAL_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("AGENT_EVAL_RUN_SOURCE", raising=False)
    configurable = target._build_configurable({"repo": "django/django"})

    assert configurable["__is_for_execution__"] is True
    assert configurable["eval"] is True
    assert configurable["source"] == "eval"
    assert configurable["repo"] == {"owner": "django", "name": "django"}
    assert "agent_model_id" not in configurable


def test_build_configurable_can_run_under_the_desktop_surface(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EVAL_RUN_SOURCE", "desktop")
    monkeypatch.setenv("AGENT_EVAL_LOCAL_PROJECT_DIR", "/srv/eval-sandbox")
    configurable = target._build_configurable({"repo": "django/django"})

    assert configurable["source"] == "desktop"
    assert configurable["local_project_path"] == "/srv/eval-sandbox"


def test_build_configurable_rejects_unknown_run_sources(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EVAL_RUN_SOURCE", "slack")
    configurable = target._build_configurable({"repo": "django/django"})

    assert configurable["source"] == "eval"


def test_desktop_surface_requires_a_local_project_dir(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EVAL_RUN_SOURCE", "desktop")
    monkeypatch.delenv("AGENT_EVAL_LOCAL_PROJECT_DIR", raising=False)
    with pytest.raises(RuntimeError, match="AGENT_EVAL_LOCAL_PROJECT_DIR"):
        target._build_configurable({"repo": "django/django"})


def test_desktop_surface_passes_the_local_project_dir(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EVAL_RUN_SOURCE", "desktop")
    monkeypatch.setenv("AGENT_EVAL_LOCAL_PROJECT_DIR", "/srv/eval-sandbox")
    configurable = target._build_configurable({"repo": "django/django"})

    assert configurable["local_project_path"] == "/srv/eval-sandbox"


def test_extract_prompt_diffs_against_the_base_commit() -> None:
    prompt = target._extract_prompt(
        {"repo": "django/django", "base_commit": "a" * 40, "problem_statement": "x"}
    )
    assert "git add -A" in prompt
    assert "git diff --cached " + "a" * 40 in prompt


def test_build_configurable_passes_model_overrides(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EVAL_MODEL_ID", "anthropic:claude-opus-5")
    monkeypatch.setenv("AGENT_EVAL_REASONING_EFFORT", "high")
    configurable = target._build_configurable({"repo": "django/django"})

    assert configurable["agent_model_id"] == "anthropic:claude-opus-5"
    assert configurable["agent_effort"] == "high"


def test_extract_final_text_prefers_the_last_ai_message() -> None:
    result = {
        "messages": [
            {"type": "human", "content": "fix the bug"},
            {"type": "tool", "content": "ok"},
            {"type": "ai", "content": ""},
            {"type": "ai", "content": "diff --git a/x b/x"},
        ]
    }
    assert target._extract_final_text(result) == "diff --git a/x b/x"


def test_extract_final_text_flattens_content_blocks() -> None:
    result = {
        "messages": [
            {
                "type": "ai",
                "content": [
                    {"type": "text", "text": "part one "},
                    {"type": "text", "text": "part two"},
                ],
            },
        ]
    }
    assert target._extract_final_text(result) == "part one part two"


def test_extract_final_text_tolerates_missing_messages() -> None:
    assert target._extract_final_text({}) == ""
    assert target._extract_final_text({"messages": "not-a-list"}) == ""


def test_solve_prompt_pins_the_base_commit_and_forbids_delivery() -> None:
    prompt = target._solve_prompt(
        {
            "repo": "django/django",
            "base_commit": "a" * 40,
            "problem_statement": "values() crashes on a joined field",
        }
    )
    assert "a" * 40 in prompt
    assert "https://github.com/django/django.git" in prompt
    assert "Do NOT open a pull request" in prompt


def test_solve_prompt_uses_the_instance_clone_url() -> None:
    prompt = target._solve_prompt(
        {
            "repo": "django/django",
            "base_commit": "a" * 40,
            "problem_statement": "values() crashes on a joined field",
            "clone_url": "https://mirror.example.com/django/django.git",
        }
    )
    assert "https://mirror.example.com/django/django.git" in prompt
    assert "https://github.com/django/django.git" not in prompt
