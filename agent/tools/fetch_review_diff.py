"""Tool: materialize the current reviewer diff in the sandbox."""

from __future__ import annotations

import re
from typing import Any

from langgraph.config import get_config

from ..review.diff import changed_files, materialize_review_diff, review_diff_range
from ..runtime import get_cached_sandbox_backend
from ..utils.sandbox_paths import aresolve_sandbox_work_dir

_MAX_CHANGED_FILES = 200
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


async def fetch_review_diff() -> dict[str, Any]:
    """Write the current review diff to a file and return bounded metadata."""
    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    if not isinstance(configurable, dict):
        return {"success": False, "error": "review context unavailable"}

    thread_id = configurable.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        return {"success": False, "error": "review thread unavailable"}
    repo = configurable.get("repo")
    repo_name = repo.get("name") if isinstance(repo, dict) else None
    if not isinstance(repo_name, str) or not _REPO_NAME_RE.fullmatch(repo_name):
        return {"success": False, "error": "review repository unavailable"}

    try:
        base_ref, head_ref, merge_base = review_diff_range(
            base_sha=str(configurable.get("base_sha", "") or ""),
            head_sha=str(configurable.get("head_sha", "") or ""),
            last_reviewed_sha=str(configurable.get("last_reviewed_sha", "") or ""),
            re_review=bool(configurable.get("re_review")),
        )
        sandbox_backend = get_cached_sandbox_backend(thread_id)
        work_dir = await aresolve_sandbox_work_dir(sandbox_backend)
        materialized = await materialize_review_diff(
            sandbox_backend,
            work_dir=f"{work_dir}/{repo_name}",
            base_ref=base_ref,
            head_ref=head_ref,
            merge_base=merge_base,
        )
    except (RuntimeError, ValueError) as exc:
        return {"success": False, "error": str(exc)}

    all_files = changed_files(materialized.diff_text)
    files = all_files[:_MAX_CHANGED_FILES]
    return {
        "success": True,
        "path": materialized.path,
        "bytes": len(materialized.diff_text.encode()),
        "files": files,
        "file_count": len(all_files),
        "files_truncated": len(all_files) > len(files),
        "base_sha": materialized.base_ref,
        "head_sha": materialized.head_ref,
        "cached": materialized.cached,
    }
