from __future__ import annotations

import os
from unittest.mock import patch

from evals.reviewer.run_eval import (
    DEFAULT_LANGSMITH_PROJECT,
    _apply_config_to_env,
    _apply_langsmith_project,
    _coerce_config,
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
                "langgraph_url": "https://example.test",
                "assistant_id": "reviewer",
                "model_id": "anthropic:claude-opus-4-8",
                "reasoning_effort": "high",
                "score_mode": "surfaced_findings",
                "severity_threshold": "high",
                "cap": 3,
            }
        )

        assert os.environ["LANGGRAPH_URL"] == "https://example.test"
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


def test_apply_langsmith_project_env_overrides_config() -> None:
    with patch.dict(os.environ, {"LANGSMITH_PROJECT": "from-env"}, clear=True):
        _apply_langsmith_project("from-config")
        assert os.environ["LANGSMITH_PROJECT"] == "from-env"
        assert os.environ["LANGCHAIN_PROJECT"] == "from-env"
