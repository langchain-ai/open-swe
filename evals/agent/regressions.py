"""Freeze selected failure evidence and replay stored predictions without model calls."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from evals.agent.dataset import Instance, TaskSet
from evals.agent.judge import InstanceResult, evaluate_target
from evals.agent.models import FailureStage, TargetResult
from evals.agent.report import write_report
from evals.agent.run_local import judge_policy


class ExpectedOutcome(BaseModel):
    resolved: bool
    failure_stage: FailureStage


class RecordedOutcome(ExpectedOutcome):
    instance_id: str


class RegressionCase(BaseModel):
    instance: Instance
    prediction: TargetResult
    expected: ExpectedOutcome


class RegressionSet(BaseModel):
    schema_version: Literal[1] = 1
    source_dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases: list[RegressionCase] = Field(min_length=1)


def freeze_cases(run_directory: Path, identities: list[str], output: Path) -> RegressionSet:
    tasks = TaskSet.model_validate_json((run_directory / "tasks.json").read_bytes())
    outcomes: dict[str, RecordedOutcome] = {}
    for line in (run_directory / "results.jsonl").read_text(encoding="utf-8").splitlines():
        row = RecordedOutcome.model_validate_json(line)
        if row.instance_id in outcomes:
            raise ValueError(f"Duplicate result: {row.instance_id}")
        outcomes[row.instance_id] = row
    predictions: dict[str, TargetResult] = {}
    for path in sorted((run_directory / "predictions").glob("*.json")):
        prediction = TargetResult.model_validate_json(path.read_bytes())
        if prediction.instance_id in predictions:
            raise ValueError(f"Duplicate prediction: {prediction.instance_id}")
        predictions[prediction.instance_id] = prediction
    by_id = {item.inputs.instance_id: item for item in tasks.instances}
    if not identities or len(set(identities)) != len(identities):
        raise ValueError("Select distinct instance IDs")
    cases: list[RegressionCase] = []
    for identity in identities:
        if identity not in by_id or identity not in outcomes or identity not in predictions:
            raise ValueError(f"Task, prediction, or outcome missing: {identity}")
        outcome = outcomes[identity]
        cases.append(
            RegressionCase(
                instance=by_id[identity],
                prediction=predictions[identity],
                expected=ExpectedOutcome(
                    resolved=outcome.resolved, failure_stage=outcome.failure_stage
                ),
            )
        )
    suite = RegressionSet(source_dataset_sha256=tasks.fingerprint, cases=cases)
    with output.open("x", encoding="utf-8") as handle:
        handle.write(suite.model_dump_json(indent=2) + "\n")
    return suite


async def replay_cases(suite: RegressionSet, directory: Path) -> bool:
    identities = [item.instance.inputs.instance_id for item in suite.cases]
    if len(set(identities)) != len(identities):
        raise ValueError("Regression set contains duplicate task IDs")
    if any(case.prediction.instance_id != case.instance.inputs.instance_id for case in suite.cases):
        raise ValueError("Prediction identity does not match its task")
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "cases.json").write_text(suite.model_dump_json(indent=2) + "\n", encoding="utf-8")
    records: list[InstanceResult] = []
    completed = False
    try:
        for case in suite.cases:
            records.append(
                await evaluate_target(
                    case.instance.inputs.model_dump(),
                    case.instance.outputs.model_dump(),
                    case.prediction.model_dump(),
                )
            )
        completed = True
    finally:
        await write_report(
            directory,
            records,
            completed=completed,
            expected_instances=len(suite.cases),
            config={
                "run_mode": "regression_replay",
                "contains_model_predictions": False,
                "source_dataset_sha256": suite.source_dataset_sha256,
                "judge_policy": judge_policy(),
            },
        )
    differences = [
        {
            "instance_id": case.instance.inputs.instance_id,
            "expected": case.expected.model_dump(mode="json"),
            "actual": {
                "resolved": result["resolved"],
                "failure_stage": result.get("failure_stage"),
            },
        }
        for case, result in zip(suite.cases, records, strict=True)
        if case.expected.resolved != result["resolved"]
        or case.expected.failure_stage != result.get("failure_stage")
    ]
    (directory / "expectations.json").write_text(
        json.dumps({"matched": not differences, "differences": differences}, indent=2) + "\n",
        encoding="utf-8",
    )
    return not differences


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--run-dir", required=True, type=Path)
    freeze.add_argument("--instance-id", required=True, action="append")
    freeze.add_argument("--output", required=True, type=Path)
    replay = commands.add_parser("replay")
    replay.add_argument("--cases", required=True, type=Path)
    replay.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "freeze":
        freeze_cases(args.run_dir, args.instance_id, args.output)
    else:
        suite = RegressionSet.model_validate_json(args.cases.read_bytes())
        if not await replay_cases(suite, args.output_dir):
            raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
