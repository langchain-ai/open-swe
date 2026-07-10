from __future__ import annotations

import os
from unittest.mock import patch

from evals.reviewer import run_eval
from evals.reviewer.run_eval import (
    DEFAULT_LANGSMITH_PROJECT,
    _apply_config_to_env,
    _apply_langsmith_project,
    _coerce_config,
    _load_env_config,
    _resolve_config,
)


def test_reviewer_eval_config_coerces_known_values() -> None:
    config = _coerce_config(
        {
            "dataset_name": "dataset",
            "experiment_prefix": "experiment",
            "max_concurrency": 2,
            "langgraph_url": "https://example.test",
            "assistant_id": "reviewer",
            "model_id": "anthropic:claude-opus-4-8",
            "reasoning_effort": "high",
            "score_mode": "surfaced_findings",
            "severity_threshold": "medium",
            "cap": 4,
            "unknown": "ignored",
        }
    )

    assert config == {
        "dataset_name": "dataset",
        "experiment_prefix": "experiment",
        "max_concurrency": 2,
        "langgraph_url": "https://example.test",
        "assistant_id": "reviewer",
        "model_id": "anthropic:claude-opus-4-8",
        "reasoning_effort": "high",
        "score_mode": "surfaced_findings",
        "severity_threshold": "medium",
        "cap": 4,
    }


def test_reviewer_eval_config_sets_target_env() -> None:
    with patch.dict(os.environ, {}, clear=True):
        _apply_config_to_env(
            {
                "dataset_name": "dataset",
                "experiment_prefix": "experiment",
                "max_concurrency": 2,
                "langgraph_url": "https://example.test",
                "langsmith_project": "project",
                "assistant_id": "reviewer",
                "model_id": "anthropic:claude-opus-4-8",
                "reasoning_effort": "high",
                "score_mode": "surfaced_findings",
                "severity_threshold": "high",
                "cap": 3,
            }
        )

        assert os.environ["REVIEWER_EVAL_DATASET_NAME"] == "dataset"
        assert os.environ["REVIEWER_EVAL_EXPERIMENT_PREFIX"] == "experiment"
        assert os.environ["REVIEWER_EVAL_MAX_CONCURRENCY"] == "2"
        assert os.environ["LANGGRAPH_URL"] == "https://example.test"
        assert os.environ["LANGSMITH_PROJECT"] == "project"
        assert os.environ["LANGCHAIN_PROJECT"] == "project"
        assert os.environ["REVIEWER_ASSISTANT_ID"] == "reviewer"
        assert os.environ["REVIEWER_EVAL_MODEL_ID"] == "anthropic:claude-opus-4-8"
        assert os.environ["REVIEWER_EVAL_REASONING_EFFORT"] == "high"
        assert os.environ["REVIEWER_EVAL_SCORE_MODE"] == "surfaced_findings"
        assert os.environ["REVIEWER_EVAL_SEVERITY_THRESHOLD"] == "high"
        assert os.environ["REVIEWER_EVAL_CAP"] == "3"


def test_reviewer_eval_config_coerces_langsmith_project() -> None:
    config = _coerce_config({"langsmith_project": "open-swe-evals"})
    assert config == {"langsmith_project": "open-swe-evals"}


def test_apply_langsmith_project_uses_config_default() -> None:
    with patch.dict(os.environ, {}, clear=True):
        _apply_langsmith_project("my-eval-project")
        assert os.environ["LANGSMITH_PROJECT"] == "my-eval-project"
        assert os.environ["LANGCHAIN_PROJECT"] == "my-eval-project"
        assert os.environ["LANGSMITH_TRACING"] == "true"


def test_apply_langsmith_project_falls_back_to_default() -> None:
    with patch.dict(os.environ, {}, clear=True):
        _apply_langsmith_project(None)
        assert os.environ["LANGSMITH_PROJECT"] == DEFAULT_LANGSMITH_PROJECT


def test_apply_langsmith_project_uses_resolved_config_over_env() -> None:
    with patch.dict(os.environ, {"LANGSMITH_PROJECT": "from-env"}, clear=True):
        _apply_langsmith_project("from-config")
        assert os.environ["LANGSMITH_PROJECT"] == "from-config"
        assert os.environ["LANGCHAIN_PROJECT"] == "from-config"


def test_load_env_config_reads_all_supported_keys() -> None:
    env = {
        "REVIEWER_EVAL_DATASET_NAME": "dataset-env",
        "REVIEWER_EVAL_EXPERIMENT_PREFIX": "experiment-env",
        "REVIEWER_EVAL_MAX_CONCURRENCY": "3",
        "LANGGRAPH_URL": "https://lg.env",
        "LANGSMITH_PROJECT": "project-env",
        "REVIEWER_ASSISTANT_ID": "reviewer-env",
        "REVIEWER_EVAL_MODEL_ID": "openai:gpt-5.6-sol",
        "REVIEWER_EVAL_REASONING_EFFORT": "xhigh",
        "REVIEWER_EVAL_SCORE_MODE": "surfaced_findings",
        "REVIEWER_EVAL_SEVERITY_THRESHOLD": "critical",
        "REVIEWER_EVAL_CAP": "1",
    }

    assert _load_env_config(env) == {
        "dataset_name": "dataset-env",
        "experiment_prefix": "experiment-env",
        "max_concurrency": 3,
        "langgraph_url": "https://lg.env",
        "langsmith_project": "project-env",
        "assistant_id": "reviewer-env",
        "model_id": "openai:gpt-5.6-sol",
        "reasoning_effort": "xhigh",
        "score_mode": "surfaced_findings",
        "severity_threshold": "critical",
        "cap": 1,
    }


def test_resolve_config_prefers_cli_then_env_then_toml() -> None:
    with (
        patch.object(
            run_eval,
            "_load_config",
            return_value={
                "dataset_name": "dataset-config",
                "experiment_prefix": "experiment-config",
                "model_id": "anthropic:claude-opus-4-8",
                "reasoning_effort": "high",
                "langsmith_project": "project-config",
            },
        ),
        patch.dict(
            os.environ,
            {
                "REVIEWER_EVAL_MODEL_ID": "google_genai:gemini-3.5-flash",
                "REVIEWER_EVAL_REASONING_EFFORT": "medium",
                "LANGSMITH_PROJECT": "project-env",
            },
            clear=True,
        ),
    ):
        config = _resolve_config(
            {
                "experiment_prefix": "experiment-cli",
                "model_id": "openai:gpt-5.6-sol",
                "reasoning_effort": "xhigh",
            }
        )

    assert config["dataset_name"] == "dataset-config"
    assert config["experiment_prefix"] == "experiment-cli"
    assert config["model_id"] == "openai:gpt-5.6-sol"
    assert config["reasoning_effort"] == "xhigh"
    assert config["langsmith_project"] == "project-env"
