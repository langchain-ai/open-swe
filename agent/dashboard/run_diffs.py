"""Persist completed run diffs independently of their sandbox."""

import json
from collections.abc import Mapping
from typing import Any

from langgraph_sdk import get_client

RUN_DIFF_NAMESPACE = ("open_swe", "run_diffs")
THREAD_DIFF_KEY = "__thread__"
_MAX_BYTES = 5 * 1024 * 1024


def _value(item: Any) -> dict[str, Any] | None:
    value = item.get("value") if isinstance(item, Mapping) else getattr(item, "value", None)
    return dict(value) if isinstance(value, Mapping) else None


def _bounded(diff: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        "status": diff.get("status", "error"),
        "truncated": bool(diff.get("truncated")),
        "files": [],
    }
    size = 0
    files = diff.get("files", [])
    for raw in files if isinstance(files, list) else []:
        if not isinstance(raw, Mapping):
            continue
        file = dict(raw)
        size += len(json.dumps(file, ensure_ascii=False).encode())
        if size > _MAX_BYTES:
            file.update(originalContent=None, modifiedContent=None, unrenderable=True)
            result["truncated"] = True
        result["files"].append(file)
    return result


async def save_run_diff(thread_id: str, turn_key: str, diff: Mapping[str, Any]) -> None:
    await get_client().store.put_item(
        (*RUN_DIFF_NAMESPACE, thread_id), turn_key, _bounded(diff), index=False
    )


async def get_run_diff(thread_id: str, turn_key: str) -> dict[str, Any] | None:
    try:
        return _value(await get_client().store.get_item((*RUN_DIFF_NAMESPACE, thread_id), turn_key))
    except Exception:
        return None
