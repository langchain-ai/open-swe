"""The reviewer-eval knobs are declared once — these pin every surface to that declaration."""

import argparse
import re
import tomllib
from pathlib import Path

from agent.review.eval_config import (
    DEFAULT_REVIEWER_EVAL_CONFIG,
    ENV_VARS,
    FIELDS,
    config_from_env,
    resolve_config,
)
from evals.reviewer.run_eval import CONFIG_PATH, _add_config_arguments, _config_from_args

WORKFLOW_PATH = Path(__file__).parents[2] / ".github" / "workflows" / "reviewer_eval.yml"

# The dispatch inputs the workflow hands to the harness, minus `limit` (a CLI-only knob).
WORKFLOW_CONFIG_INPUTS = (
    "model_id",
    "reasoning_effort",
    "dataset_name",
    "experiment_prefix",
    "max_concurrency",
    "score_mode",
    "severity_threshold",
    "cap",
    "langsmith_project",
    "assistant_id",
)


def _workflow_input_env() -> dict[str, str]:
    """``{workflow input: env var it is exported as}`` from the eval workflow."""
    text = WORKFLOW_PATH.read_text()
    pairs = re.findall(r"^ +([A-Z_]+): \$\{\{ inputs\.(\w+) \}\}$", text, re.MULTILINE)
    return {input_name: env_var for env_var, input_name in pairs}


def test_defaults_cover_every_field():
    assert set(DEFAULT_REVIEWER_EVAL_CONFIG) == {field.name for field in FIELDS}
    assert DEFAULT_REVIEWER_EVAL_CONFIG == {
        "dataset_name": "openswe-reviewer-v1",
        "experiment_prefix": "openswe-review-confidence",
        "max_concurrency": 5,
        "langsmith_project": "open-swe-evals",
        "langgraph_url": "",
        "assistant_id": "reviewer",
        "model_id": "google_genai:gemini-3.7-flash",
        "reasoning_effort": "medium",
        "score_mode": "surfaced_findings",
        "severity_threshold": "low",
        "cap": 6,
    }


def test_later_layers_win():
    resolved = resolve_config(
        {"model_id": "openai:gpt-5.6-sol", "cap": 3},
        {"cap": 4},
        {"score_mode": "all_findings"},
    )
    assert resolved["model_id"] == "openai:gpt-5.6-sol"
    assert resolved["cap"] == 4
    assert resolved["score_mode"] == "all_findings"
    assert resolved["dataset_name"] == "openswe-reviewer-v1"


def test_unusable_values_never_override_defaults():
    resolved = resolve_config(
        {
            "score_mode": "sometimes",
            "severity_threshold": "spicy",
            "max_concurrency": 0,
            "dataset_name": "",
            "cap": "not-a-number",
            "unknown_knob": "ignored",
        }
    )
    assert resolved == DEFAULT_REVIEWER_EVAL_CONFIG


def test_env_layer_reads_each_field_from_its_env_var():
    assert config_from_env(
        {
            "REVIEWER_EVAL_MAX_CONCURRENCY": "12",
            "REVIEWER_EVAL_CAP": "0",
            "REVIEWER_EVAL_SCORE_MODE": "all_findings",
            "LANGSMITH_PROJECT": "scratch-project",
            "UNRELATED_VAR": "ignored",
        }
    ) == {
        "max_concurrency": 12,
        "cap": 0,
        "score_mode": "all_findings",
        "langsmith_project": "scratch-project",
    }


def test_cli_flags_are_generated_from_the_field_table():
    parser = argparse.ArgumentParser()
    _add_config_arguments(parser)
    args = parser.parse_args(["--severity-threshold", "high", "--max-concurrency", "3"])
    assert _config_from_args(args) == {"severity_threshold": "high", "max_concurrency": 3}


def test_config_toml_declares_exactly_the_known_fields():
    with CONFIG_PATH.open("rb") as f:
        raw = tomllib.load(f)
    assert set(raw) == set(ENV_VARS)
    assert raw["experiment_prefix"] == DEFAULT_REVIEWER_EVAL_CONFIG["experiment_prefix"]


def test_workflow_exports_each_config_input_under_its_shared_env_var():
    assert _workflow_input_env() == {
        **{name: ENV_VARS[name] for name in WORKFLOW_CONFIG_INPUTS},
        "limit": "INPUT_LIMIT",
    }


def test_workflow_invokes_run_eval_without_config_flags():
    text = WORKFLOW_PATH.read_text()
    assert 'uv run python -m evals.reviewer.run_eval "${limit_args[@]}"' in text
