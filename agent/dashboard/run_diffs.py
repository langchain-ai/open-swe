"""Persist completed run diffs independently of their sandbox."""

import json
from collections.abc import Mapping
from typing import Any

from ..store import get_value, store_client

RUN_DIFF_NAMESPACE = ("open_swe", "run_diffs")
THREAD_DIFF_KEY = "__thread__"
_MAX_BYTES = 5 * 1024 * 1024


def _bounded(diff: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        "status": diff.get("status", "error"),
        "truncated": bool(diff.get("truncated")),
        "summary": diff.get("summary", {"files": 0, "additions": 0, "deletions": 0}),
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


def project_run_diff(
    diff: Mapping[str, Any], *, max_files: int, include_content: bool
) -> dict[str, Any]:
    result = dict(diff)
    files = diff.get("files", [])
    files = (
        [dict(file) for file in files if isinstance(file, Mapping)]
        if isinstance(files, list)
        else []
    )
    result["files"] = files[:max_files]
    result["truncated"] = bool(diff.get("truncated")) or len(files) > max_files
    if not include_content:
        for file in result["files"]:
            file.update(originalContent=None, modifiedContent=None)
    return result


async def save_run_diff(thread_id: str, turn_key: str, diff: Mapping[str, Any]) -> None:
    # Not ``store.put_value``: a diff is opaque blob data, and letting the store
    # index it would embed megabytes of file contents for nothing.
    await store_client().store.put_item(
        [*RUN_DIFF_NAMESPACE, thread_id], turn_key, _bounded(diff), index=False
    )


async def get_run_diff(thread_id: str, turn_key: str) -> dict[str, Any] | None:
    try:
        return await get_value((*RUN_DIFF_NAMESPACE, thread_id), turn_key)
    except Exception:
        # A stored diff is a cache: on a store outage the caller falls back to
        # reading the sandbox's own checkpoint rather than failing the panel.
        return None
