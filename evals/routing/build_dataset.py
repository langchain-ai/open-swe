"""Upload evals/routing/tasks.json as a LangSmith dataset.

Usage:
    uv run python -m evals.routing.build_dataset --dataset-name openswe-routing-v1
    uv run python -m evals.routing.build_dataset --dry-run
"""

import argparse
import json
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from langsmith import Client
from pydantic import BaseModel, TypeAdapter

TASKS_PATH = Path(__file__).with_name("tasks.json")
DEFAULT_DATASET_NAME = "openswe-routing-v1"
DEFAULT_REPO = "langchain-ai/open-swe"

ExpectedRoute = Literal["fast", "balanced", "performance", "none"]


class RoutingTask(BaseModel):
    id: str
    prompt: str
    expected_route: ExpectedRoute
    rationale: str = ""


_TASKS = TypeAdapter(list[RoutingTask])


def load_tasks() -> list[RoutingTask]:
    tasks = _TASKS.validate_json(TASKS_PATH.read_text(encoding="utf-8"))
    ids = [task.id for task in tasks]
    duplicates = {task_id for task_id in ids if ids.count(task_id) > 1}
    if duplicates:
        raise ValueError(f"duplicate task ids: {sorted(duplicates)}")
    return tasks


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-name", default=DEFAULT_DATASET_NAME)
    parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/name the tasks target")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    tasks = load_tasks()
    inputs = [{"task_id": task.id, "prompt": task.prompt, "repo": args.repo} for task in tasks]
    outputs = [
        {"expected_route": task.expected_route, "rationale": task.rationale} for task in tasks
    ]
    if args.dry_run:
        print(
            json.dumps(
                [{"inputs": i, "outputs": o} for i, o in zip(inputs, outputs, strict=True)],
                indent=2,
            )
        )
        return

    client = Client()
    dataset = (
        client.read_dataset(dataset_name=args.dataset_name)
        if client.has_dataset(dataset_name=args.dataset_name)
        else client.create_dataset(
            dataset_name=args.dataset_name,
            description=f"Pre-routed mode routing decisions against {args.repo}.",
        )
    )
    client.create_examples(inputs=inputs, outputs=outputs, dataset_id=dataset.id)
    print(f"uploaded {len(tasks)} examples to {args.dataset_name} ({dataset.id})")


if __name__ == "__main__":
    main()
