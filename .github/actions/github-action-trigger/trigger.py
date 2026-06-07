#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import uuid
import urllib.error
import urllib.request


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

    body = {
        "assistant_id": assistant_id,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ]
        },
        "if_not_exists": if_not_exists,
    }
    body_bytes = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{open_swe_url}/threads/{thread_id}/runs",
        data=body_bytes,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": open_swe_api_key,
        },
    )

    try:
        with urllib.request.urlopen(request) as response:
            response_text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"Open SWE runs.create call failed with HTTP {exc.code}", file=sys.stderr)
        if body:
            print(body, file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Open SWE runs.create request failed: {exc.reason}", file=sys.stderr)
        return 1

    try:
        response_json_obj = json.loads(response_text) if response_text else {}
    except json.JSONDecodeError:
        print("Open SWE runs.create response was not valid JSON", file=sys.stderr)
        return 1

    response_json = json.dumps(response_json_obj, separators=(",", ":"))
    run_id = response_json_obj.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        candidate = response_json_obj.get("id")
        run_id = candidate if isinstance(candidate, str) else ""

    _write_output("thread_id", thread_id)
    _write_output("run_id", run_id)
    _write_output("response_json", response_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
