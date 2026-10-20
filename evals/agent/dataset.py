"""Strict, content-addressed task sets for reproducible local experiments."""

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.agent.models import EvalInputs, GradingSpec


class Instance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs: EvalInputs
    outputs: GradingSpec
    gold_patch: str = ""


class TaskSet(BaseModel):
    schema_version: Literal[1] = 1
    instances: list[Instance] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_instances(self) -> TaskSet:
        identities = [item.inputs.instance_id for item in self.instances]
        if len(set(identities)) != len(identities):
            raise ValueError("Task set contains duplicate instance IDs")
        return self

    @property
    def fingerprint(self) -> str:
        payload = [
            instance.model_dump(mode="json")
            for instance in sorted(self.instances, key=lambda item: item.inputs.instance_id)
        ]
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()


def load_task_set(path: Path, *, limit: int | None = None) -> TaskSet:
    """Reject malformed or duplicate rows instead of silently shrinking the denominator."""
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    instances: list[Instance] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        raw: object = json.loads(line)
        if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
            raise ValueError(f"Line {line_number} must be a JSON object")
        patch = raw.get("patch", "")
        if not isinstance(patch, str):
            raise ValueError(f"Line {line_number}: patch must be a string")
        instance = Instance(
            inputs=EvalInputs.model_validate(raw),
            outputs=GradingSpec.model_validate(raw),
            gold_patch=patch,
        )
        if instance.inputs.instance_id in seen:
            raise ValueError(f"Duplicate instance: {instance.inputs.instance_id}")
        seen.add(instance.inputs.instance_id)
        instances.append(instance)
    return TaskSet(instances=instances[:limit] if limit is not None else instances)
