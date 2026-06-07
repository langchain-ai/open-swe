#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import uuid

from langgraph_sdk import get_sync_client


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _write_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as f:
        f.write(f"{name}={value}\n")


def _to_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, dict):
            return dumped
    as_dict = getattr(value, "dict", None)
    if callable(as_dict):
        dumped = as_dict()
        if isinstance(dumped, dict):
            return dumped
    raise TypeError("runs.create returned an unexpected response type")


def main() -> int:
    try:
        open_swe_url = _required_env("INPUT_OPEN_SWE_URL").rstrip("/")
        open_swe_api_key = _required_env("INPUT_OPEN_SWE_API_KEY")
        prompt = _required_env("INPUT_PROMPT")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    assistant_id = os.environ.get("INPUT_ASSISTANT_ID", "").strip() or "agent"
    thread_id = os.environ.get("INPUT_THREAD_ID", "").strip() or str(uuid.uuid4())
    if_not_exists = os.environ.get("INPUT_IF_NOT_EXISTS", "").strip() or "create"
    if if_not_exists not in {"create", "reject"}:
        print("INPUT_IF_NOT_EXISTS must be 'create' or 'reject'", file=sys.stderr)
        return 1

    try:
        client = get_sync_client(url=open_swe_url, api_key=open_swe_api_key)
        run = client.runs.create(
            thread_id=thread_id,
            assistant_id=assistant_id,
            input={
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ]
            },
            if_not_exists=if_not_exists,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Open SWE runs.create call failed: {exc}", file=sys.stderr)
        return 1

    try:
        response_obj = _to_dict(run)
    except TypeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    response_json = json.dumps(response_obj, separators=(",", ":"), default=str)
    run_id = response_obj.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        candidate = response_obj.get("id")
        run_id = candidate if isinstance(candidate, str) else ""

    _write_output("thread_id", thread_id)
    _write_output("run_id", run_id)
    _write_output("response_json", response_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
