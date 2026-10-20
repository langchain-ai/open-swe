"""Compare complete agent experiments with identical tasks and judge policies."""

import argparse
import asyncio
import json
from pathlib import Path
from statistics import median

from pydantic import BaseModel, Field

from agent.prompts import render_prompt
from evals.agent.dataset import TaskSet
from evals.agent.models import FailureStage


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


def _load(directory: Path) -> tuple[_Summary, dict[str, _Row]]:
    summary = _Summary.model_validate_json((directory / "summary.json").read_bytes())
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
    return summary, rows


def compare_runs(baseline: Path, candidate: Path) -> dict[str, object]:
    before, left = _load(baseline)
    after, right = _load(candidate)
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
    return {
        "schema_version": 1,
        "baseline": str(baseline),
        "candidate": str(candidate),
        "dataset_sha256": before.config.dataset_sha256,
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
        "instances": [
            {
                "instance_id": key,
                "baseline_stage": left[key].failure_stage,
                "candidate_stage": right[key].failure_stage,
                "baseline_resolved": left[key].resolved,
                "candidate_resolved": right[key].resolved,
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
