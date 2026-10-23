"""Compare complete agent experiments with identical tasks and judge policies."""

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path
from statistics import median

from pydantic import BaseModel, Field

from agent.prompts import render_prompt
from evals.agent.dataset import TaskSet
from evals.agent.models import FailureStage

_CONDITION_KEYS = (
    "model_id",
    "reasoning_effort",
    "compaction_trigger_tokens",
    "compaction_trigger_messages",
    "compaction_trigger_fraction",
    "compaction_summary_prompt",
)


class _RunConfig(BaseModel):
    run_mode: str
    contains_model_predictions: bool
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    judge_policy: dict[str, object]
    max_concurrency: int = Field(ge=1)


class _Summary(BaseModel):
    schema_version: int
    completed: bool
    expected_instances: int
    evaluated_instances: int
    config: _RunConfig


class _Compaction(BaseModel):
    count: int = Field(default=0, ge=0)
    messages_summarized: int = Field(default=0, ge=0)
    summarized_tokens: int = Field(default=0, ge=0)
    summary_tokens: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    overflow_count: int = Field(default=0, ge=0)


class _Row(BaseModel):
    instance_id: str
    repo: str
    resolved: bool
    failure_stage: FailureStage
    wall_clock_seconds: float = Field(ge=0, allow_inf_nan=False)
    steps: int | None = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    usage_complete: bool = False
    compaction: _Compaction | None = None


def _load(directory: Path) -> tuple[_Summary, dict[str, _Row], dict[str, object]]:
    raw_summary: object = json.loads((directory / "summary.json").read_bytes())
    summary = _Summary.model_validate(raw_summary)
    config = raw_summary.get("config", {}) if isinstance(raw_summary, dict) else {}
    if not isinstance(config, dict):
        config = {}
    if summary.schema_version != 1 or not summary.completed:
        raise ValueError(f"Experiment is incomplete or unsupported: {directory}")
    if summary.config.run_mode != "agent" or not summary.config.contains_model_predictions:
        raise ValueError("Comparisons require model predictions, not reference/empty self-checks")
    tasks = TaskSet.model_validate_json((directory / "tasks.json").read_bytes())
    if tasks.fingerprint != summary.config.dataset_sha256:
        raise ValueError("Task snapshot does not match the recorded dataset fingerprint")
    rows: dict[str, _Row] = {}
    for line in (directory / "results.jsonl").read_text(encoding="utf-8").splitlines():
        row = _Row.model_validate_json(line)
        if row.instance_id in rows:
            raise ValueError(f"Duplicate result: {row.instance_id}")
        if row.resolved != (row.failure_stage == FailureStage.NONE):
            raise ValueError(f"Inconsistent resolution and failure stage: {row.instance_id}")
        rows[row.instance_id] = row
    expected = {item.inputs.instance_id: item.inputs.repo for item in tasks.instances}
    if (
        {key: row.repo for key, row in rows.items()} != expected
        or len(rows) != summary.expected_instances
        or len(rows) != summary.evaluated_instances
    ):
        raise ValueError("Missing, unexpected, or mismatched experiment results")
    return summary, rows, config


def _condition(config: dict[str, object]) -> dict[str, object]:
    return {key: config[key] for key in _CONDITION_KEYS if key in config}


def _compaction(row: _Row) -> _Compaction:
    return row.compaction or _Compaction()


def compare_runs(baseline: Path, candidate: Path) -> dict[str, object]:
    before, left, baseline_config = _load(baseline)
    after, right, candidate_config = _load(candidate)
    if before.config.dataset_sha256 != after.config.dataset_sha256:
        raise ValueError("Cannot compare different task sets")
    if (
        before.config.judge_policy != after.config.judge_policy
        or before.config.max_concurrency != after.config.max_concurrency
    ):
        raise ValueError("Cannot compare different judge policies or concurrency")
    count = len(left)
    improvements = [key for key in left if not left[key].resolved and right[key].resolved]
    regressions = [key for key in left if left[key].resolved and not right[key].resolved]
    step_deltas = [
        new - old
        for key in left
        if (old := left[key].steps) is not None and (new := right[key].steps) is not None
    ]
    token_deltas = [
        new_in + new_out - old_in - old_out
        for key in left
        if left[key].usage_complete
        and right[key].usage_complete
        and (old_in := left[key].input_tokens) is not None
        and (old_out := left[key].output_tokens) is not None
        and (new_in := right[key].input_tokens) is not None
        and (new_out := right[key].output_tokens) is not None
    ]
    compaction_pairs = [
        (old, new)
        for key in left
        if (old := left[key].compaction) and (new := right[key].compaction)
    ]
    baseline_compactions = [_compaction(left[key]).count for key in left]
    candidate_compactions = [_compaction(right[key]).count for key in left]
    transitions = Counter(
        (str(left[key].failure_stage), str(right[key].failure_stage)) for key in left
    )
    return {
        "schema_version": 1,
        "baseline": str(baseline),
        "candidate": str(candidate),
        "dataset_sha256": before.config.dataset_sha256,
        "baseline_condition": _condition(baseline_config),
        "candidate_condition": _condition(candidate_config),
        "paired_instances": count,
        "baseline_resolve_rate": sum(row.resolved for row in left.values()) / count,
        "candidate_resolve_rate": sum(row.resolved for row in right.values()) / count,
        "resolve_rate_delta_percentage_points": 100
        * (len(improvements) - len(regressions))
        / count,
        "improved_instance_ids": improvements,
        "regressed_instance_ids": regressions,
        "median_paired_agent_seconds_delta": median(
            right[key].wall_clock_seconds - left[key].wall_clock_seconds for key in left
        ),
        "observed_step_pairs": len(step_deltas),
        "median_paired_steps_delta": median(step_deltas) if step_deltas else None,
        "complete_usage_pairs": len(token_deltas),
        "median_paired_main_agent_tokens_delta": median(token_deltas) if token_deltas else None,
        "baseline_compaction_rate": sum(value > 0 for value in baseline_compactions) / count,
        "candidate_compaction_rate": sum(value > 0 for value in candidate_compactions) / count,
        "mean_compactions_delta": (sum(candidate_compactions) - sum(baseline_compactions)) / count,
        "compaction_pairs": len(compaction_pairs),
        "median_paired_compaction_count_delta": (
            median(new.count - old.count for old, new in compaction_pairs)
            if compaction_pairs
            else None
        ),
        "complete_compaction_token_pairs": sum(
            1 for old, new in compaction_pairs if old.summary_tokens and new.summary_tokens
        ),
        "median_paired_summarized_tokens_delta": (
            median(new.summarized_tokens - old.summarized_tokens for old, new in compaction_pairs)
            if compaction_pairs
            else None
        ),
        "median_paired_summary_tokens_delta": (
            median(new.summary_tokens - old.summary_tokens for old, new in compaction_pairs)
            if compaction_pairs
            else None
        ),
        "baseline_overflow_compactions": sum(_compaction(left[key]).overflow_count for key in left),
        "candidate_overflow_compactions": sum(
            _compaction(right[key]).overflow_count for key in left
        ),
        "failure_stage_transitions": [
            {"baseline_stage": baseline_stage, "candidate_stage": candidate_stage, "instances": n}
            for (baseline_stage, candidate_stage), n in sorted(transitions.items())
        ],
        "instances": [
            {
                "instance_id": key,
                "baseline_stage": left[key].failure_stage,
                "candidate_stage": right[key].failure_stage,
                "baseline_resolved": left[key].resolved,
                "candidate_resolved": right[key].resolved,
                "baseline_compactions": _compaction(left[key]).count,
                "candidate_compactions": _compaction(right[key]).count,
            }
            for key in left
        ],
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    comparison = compare_runs(args.baseline, args.candidate)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "report.md").write_text(
        render_prompt(
            "evals/agent-comparison.md",
            comparison=json.dumps(comparison, ensure_ascii=False, indent=2),
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    asyncio.run(main())
