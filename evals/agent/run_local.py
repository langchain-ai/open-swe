"""Run pinned JSONL tasks without uploading a LangSmith dataset."""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import platform
import sys
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

from evals.agent.dataset import TaskSet, load_task_set
from evals.agent.judge import InstanceResult, evaluate_target, python_command, test_timeout_seconds
from evals.agent.models import TargetResult
from evals.agent.report import write_report
from evals.agent.target import drain_thread_ids, solve_task

from .run_eval import _apply_config_to_env, _cleanup_threads, _resolve_config

logger = logging.getLogger(__name__)
RunMode = Literal["agent", "reference", "empty"]


def judge_policy() -> dict[str, object]:
    """Record scoring code and host policy separately from the variable under experiment."""
    scoring_hash = hashlib.sha256()
    for name in ("judge.py", "models.py"):
        scoring_hash.update(Path(__file__).with_name(name).read_bytes())
    return {
        "scoring_sha256": scoring_hash.hexdigest(),
        "platform": platform.platform(),
        "runner_python": sys.version,
        "judge_python": python_command(),
        "test_timeout_seconds": test_timeout_seconds(),
    }


async def run_local(
    tasks: TaskSet,
    directory: Path,
    *,
    mode: RunMode,
    config: dict[str, object],
    max_concurrency: int = 1,
) -> list[InstanceResult]:
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be positive")
    if mode == "reference" and any(not item.gold_patch.strip() for item in tasks.instances):
        raise ValueError("Every reference task must contain a nonempty patch")
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "tasks.json").write_text(tasks.model_dump_json(indent=2) + "\n", encoding="utf-8")
    semaphore = asyncio.Semaphore(max_concurrency)
    records: list[InstanceResult] = []
    completed = False
    run_error: BaseException | None = None

    async def evaluate(index: int) -> None:
        instance = tasks.instances[index]
        async with semaphore:
            inputs = instance.inputs.model_dump()
            if mode == "agent":
                prediction = TargetResult.model_validate(await solve_task(inputs))
            else:
                prediction = TargetResult(
                    instance_id=instance.inputs.instance_id,
                    repo=instance.inputs.repo,
                    patch=instance.gold_patch if mode == "reference" else "",
                )
            predictions_dir = directory / "predictions"
            predictions_dir.mkdir(exist_ok=True)
            (predictions_dir / f"{index:06d}.json").write_text(
                prediction.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )
            result = await evaluate_target(
                inputs, instance.outputs.model_dump(), prediction.model_dump()
            )
            records.append(result)
            logger.info(
                "Local evaluation task completed",
                extra={
                    "instance_id": result["instance_id"],
                    "failure_stage": result.get("failure_stage"),
                    "resolved": result["resolved"],
                },
            )

    try:
        async with asyncio.TaskGroup() as group:
            for index in range(len(tasks.instances)):
                group.create_task(evaluate(index))
        completed = True
    except BaseException as exc:
        run_error = exc
        raise
    finally:
        records.sort(key=lambda item: item["instance_id"])
        try:
            await write_report(
                directory,
                records,
                config={
                    **config,
                    "run_mode": mode,
                    "contains_model_predictions": mode == "agent",
                    "dataset_sha256": tasks.fingerprint,
                    "judge_policy": judge_policy(),
                    "max_concurrency": max_concurrency,
                },
                completed=completed,
                expected_instances=len(tasks.instances),
            )
        except Exception:
            logger.exception("Failed to save local evaluation report")
            if run_error is None:
                raise
    return records


async def main() -> None:
    # AGENT_EVAL_ENV_FILE lets an experiment load its own env file instead of
    # the developer's .env, which may point at a different model endpoint.
    env_file = os.environ.get("AGENT_EVAL_ENV_FILE")
    load_dotenv(env_file if env_file else None)
    if env_file:
        logger.info("Loaded evaluation environment from %s", env_file)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instances", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--mode", choices=["agent", "reference", "empty"], default="agent")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-concurrency", type=int, default=1)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    # Local evaluation does not opt into external tracing on the user's behalf.
    os.environ.setdefault("LANGSMITH_TRACING", "false")
    config = _resolve_config()
    _apply_config_to_env(config)
    tasks = load_task_set(args.instances, limit=args.limit)
    mode: RunMode = args.mode
    try:
        await run_local(
            tasks,
            args.output_dir,
            mode=mode,
            config=dict(config),
            max_concurrency=args.max_concurrency,
        )
    finally:
        if not args.no_cleanup and (thread_ids := drain_thread_ids()):
            await _cleanup_threads(thread_ids)
    print(json.dumps({"output_dir": str(args.output_dir), "dataset_sha256": tasks.fingerprint}))


if __name__ == "__main__":
    asyncio.run(main())
