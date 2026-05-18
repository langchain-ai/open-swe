from __future__ import annotations

import os
from unittest.mock import patch

from evals.reviewer.run_eval import _apply_config_to_env, _coerce_config


def test_reviewer_eval_config_coerces_known_values() -> None:
    config = _coerce_config(
        {
            "dataset_name": "dataset",
            "experiment_prefix": "experiment",
            "max_concurrency": 2,
            "langgraph_url": "https://example.test",
            "assistant_id": "reviewer",
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
                "score_mode": "surfaced_findings",
                "severity_threshold": "high",
                "cap": 3,
            }
        )

        assert os.environ["LANGGRAPH_URL"] == "https://example.test"
        assert os.environ["REVIEWER_ASSISTANT_ID"] == "reviewer"
        assert os.environ["REVIEWER_EVAL_SCORE_MODE"] == "surfaced_findings"
        assert os.environ["REVIEWER_EVAL_SEVERITY_THRESHOLD"] == "high"
        assert os.environ["REVIEWER_EVAL_CAP"] == "3"
