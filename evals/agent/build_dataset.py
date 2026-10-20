"""Build the LangSmith dataset for the agent eval.

Reads SWE-bench-style instances from a JSONL file and uploads one example per
instance. Grading data (test ids, test patch, gold patch, install commands)
travels in ``outputs`` — the target only reads ``inputs``, so the agent is
never shown how it will be scored.

Usage:
    uv run python -m evals.agent.build_dataset \\
        --instances evals/agent/instances/swe_bench_verified.jsonl \\
        --dataset-name openswe-agent-v1
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langsmith import Client

_REQUIRED_STR_FIELDS = ("instance_id", "repo", "base_commit", "problem_statement")
_REQUIRED_LIST_FIELDS = ("fail_to_pass",)


class InstanceError(ValueError):
    """One JSONL row is not a usable benchmark instance."""


def _require_str(raw: dict[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise InstanceError(f"{field} must be a non-empty string")
    return value


def _require_test_ids(raw: dict[str, Any], field: str) -> list[str]:
    value = raw.get(field)
    if not isinstance(value, list) or not value:
        raise InstanceError(f"{field} must be a non-empty list of test ids")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise InstanceError(f"{field} must contain only non-empty test id strings")
    return list(value)


def _optional_test_ids(raw: dict[str, Any], field: str) -> list[str]:
    value = raw.get(field)
    if value is None:
        return []
    return _require_test_ids({**raw, field: value}, field)


def _optional_str(raw: dict[str, Any], field: str) -> str:
    value = raw.get(field)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise InstanceError(f"{field} must be a string when present")
    return value


def _optional_commands(raw: dict[str, Any], field: str) -> list[str]:
    value = raw.get(field)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InstanceError(f"{field} must be a list of shell command strings")
    return list(value)


def build_example(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate one instance row and split it into inputs and grading outputs."""
    for field in _REQUIRED_STR_FIELDS:
        _require_str(raw, field)
    for field in _REQUIRED_LIST_FIELDS:
        _require_test_ids(raw, field)

    inputs = {field: raw[field] for field in _REQUIRED_STR_FIELDS}
    # clone_url rides in inputs: the agent clones from it, so it must see it.
    clone_url = _optional_str(raw, "clone_url")
    if clone_url:
        inputs["clone_url"] = clone_url
    outputs = {
        "fail_to_pass": _require_test_ids(raw, "fail_to_pass"),
        "pass_to_pass": _optional_test_ids(raw, "pass_to_pass"),
        "test_patch": _optional_str(raw, "test_patch"),
        "gold_patch": _optional_str(raw, "patch"),
        "install_commands": _optional_commands(raw, "install_commands"),
        "test_command": _optional_str(raw, "test_command") or "pytest",
    }
    return {
        "inputs": inputs,
        "outputs": outputs,
        "metadata": {"instance_id": raw["instance_id"], "repo": raw["repo"]},
    }


def load_instances(path: Path) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    with path.open() as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"  line {line_number}: invalid JSON: {exc}", file=sys.stderr)
                continue
            if not isinstance(raw, dict):
                print(f"  line {line_number}: not a JSON object", file=sys.stderr)
                continue
            try:
                examples.append(build_example(raw))
            except InstanceError as exc:
                print(
                    f"  line {line_number} ({raw.get('instance_id', '?')}): {exc}", file=sys.stderr
                )
    return examples


def upload(dataset_name: str, examples: list[dict[str, Any]]) -> None:
    client = Client()
    existing = next((d for d in client.list_datasets(dataset_name=dataset_name)), None)
    if existing:
        print(
            f"Dataset {dataset_name!r} already exists ({existing.id}); aborting.", file=sys.stderr
        )
        print("Delete it in the LangSmith UI or pass --dataset-name <new>.", file=sys.stderr)
        sys.exit(1)
    dataset = client.create_dataset(
        dataset_name=dataset_name,
        description=(
            "Open SWE agent baseline — SWE-bench-style instances. "
            "Resolved when fail_to_pass tests pass and pass_to_pass tests keep passing."
        ),
    )
    client.create_examples(
        dataset_id=dataset.id,
        inputs=[example["inputs"] for example in examples],
        outputs=[example["outputs"] for example in examples],
        metadata=[example["metadata"] for example in examples],
    )
    print(f"Uploaded {len(examples)} examples to {dataset_name} ({dataset.id}).")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--instances", default="evals/agent/instances/swe_bench_verified.jsonl")
    parser.add_argument("--dataset-name", default="openswe-agent-v1")
    parser.add_argument("--dry-run", action="store_true", help="Build examples but don't upload.")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    path = Path(args.instances)
    if not path.exists():
        print(f"Instances file not found: {path}", file=sys.stderr)
        sys.exit(1)

    examples = load_instances(path)
    if args.limit:
        examples = examples[: args.limit]
    print(f"Loaded {len(examples)} valid instances from {path}")
    if not examples:
        sys.exit(1)

    if args.dry_run:
        out = path.with_name("dataset_dryrun.json")
        with out.open("w") as handle:
            json.dump(examples, handle, indent=2)
        print(f"Dry run: wrote {len(examples)} examples to {out}")
        return

    upload(args.dataset_name, examples)


if __name__ == "__main__":
    main()
