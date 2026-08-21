"""One declaration of the reviewer-eval knobs.

The same eleven settings arrive from four directions: ``evals/reviewer/config.toml``,
the env vars the ``Reviewer eval`` GitHub Action exports, ``run_eval``'s CLI flags, and
the config snapshot the dashboard stores alongside a run. ``FIELDS`` is the only place
they are spelled out — the defaults, the env reader, the CLI and the validation are all
derived from it.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypedDict, cast

from agent.review.findings import REVIEW_FINDING_CAP, Severity

DEFAULT_EVAL_PROJECT = "open-swe-evals"

ScoreMode = Literal["all_findings", "surfaced_findings"]

SCORE_MODES: tuple[ScoreMode, ...] = ("all_findings", "surfaced_findings")
SEVERITIES: tuple[Severity, ...] = ("low", "medium", "high", "critical")


class ReviewerEvalConfig(TypedDict):
    dataset_name: str
    experiment_prefix: str
    max_concurrency: int
    langsmith_project: str
    langgraph_url: str
    assistant_id: str
    model_id: str
    reasoning_effort: str
    score_mode: ScoreMode
    severity_threshold: Severity
    cap: int


@dataclass(frozen=True)
class EvalConfigField:
    name: str
    env_var: str
    default: str | int
    choices: tuple[str, ...] = ()
    minimum: int = 0

    @property
    def cli_flag(self) -> str:
        return "--" + self.name.replace("_", "-")

    @property
    def is_int(self) -> bool:
        return isinstance(self.default, int)

    def coerce(self, value: Any) -> str | int | None:
        """The value in this field's type, or ``None`` when it is unusable."""
        if self.is_int:
            parsed = _as_int(value)
            return parsed if parsed is not None and parsed >= self.minimum else None
        if not isinstance(value, str) or not value:
            return None
        if self.choices and value not in self.choices:
            return None
        return value


FIELDS: tuple[EvalConfigField, ...] = (
    EvalConfigField("dataset_name", "REVIEWER_EVAL_DATASET_NAME", "openswe-reviewer-v1"),
    EvalConfigField(
        "experiment_prefix", "REVIEWER_EVAL_EXPERIMENT_PREFIX", "openswe-review-confidence"
    ),
    EvalConfigField("max_concurrency", "REVIEWER_EVAL_MAX_CONCURRENCY", 5, minimum=1),
    EvalConfigField("langsmith_project", "LANGSMITH_PROJECT", DEFAULT_EVAL_PROJECT),
    EvalConfigField("langgraph_url", "LANGGRAPH_URL", ""),
    EvalConfigField("assistant_id", "REVIEWER_ASSISTANT_ID", "reviewer"),
    EvalConfigField("model_id", "REVIEWER_EVAL_MODEL_ID", "google_genai:gemini-3.7-flash"),
    EvalConfigField("reasoning_effort", "REVIEWER_EVAL_REASONING_EFFORT", "medium"),
    EvalConfigField(
        "score_mode", "REVIEWER_EVAL_SCORE_MODE", "surfaced_findings", choices=SCORE_MODES
    ),
    EvalConfigField(
        "severity_threshold", "REVIEWER_EVAL_SEVERITY_THRESHOLD", "low", choices=SEVERITIES
    ),
    EvalConfigField("cap", "REVIEWER_EVAL_CAP", REVIEW_FINDING_CAP),
)

ENV_VARS: dict[str, str] = {field.name: field.env_var for field in FIELDS}

DEFAULT_REVIEWER_EVAL_CONFIG: ReviewerEvalConfig = cast(
    ReviewerEvalConfig, {field.name: field.default for field in FIELDS}
)


def coerce_config(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only known knobs whose value fits the field."""
    config: dict[str, Any] = {}
    for field in FIELDS:
        if field.name not in raw:
            continue
        value = field.coerce(raw[field.name])
        if value is not None:
            config[field.name] = value
    return config


def config_from_env(env: Mapping[str, str] = os.environ) -> dict[str, Any]:
    return coerce_config(
        {field.name: env[field.env_var] for field in FIELDS if field.env_var in env}
    )


def resolve_config(*layers: Mapping[str, Any]) -> ReviewerEvalConfig:
    """Merge partial layers over the defaults; later layers win."""
    resolved: dict[str, Any] = dict(DEFAULT_REVIEWER_EVAL_CONFIG)
    for layer in layers:
        resolved.update(coerce_config(layer))
    return cast(ReviewerEvalConfig, resolved)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    try:
        return int(value)
    except ValueError:
        return None
