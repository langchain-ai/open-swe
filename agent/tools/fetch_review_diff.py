"""Tool: materialize the current reviewer diff in the sandbox."""

import re
from typing import Any

from agent.review.diff import changed_files, materialize_review_diff, review_diff_range
from agent.run_config import RunConfig
from agent.runtime import get_cached_sandbox_backend
from agent.sandboxes.paths import resolve_sandbox_work_dir

_MAX_CHANGED_FILES = 200
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


async def fetch_review_diff() -> dict[str, Any]:
    """Write the current review diff to a file and return bounded metadata."""
    cfg = RunConfig.from_runtime()

    thread_id = cfg.thread_id
    if not thread_id:
        return {"success": False, "error": "review thread unavailable"}
    repo_name = cfg.repo.name if cfg.repo else ""
    if not _REPO_NAME_RE.fullmatch(repo_name):
        return {"success": False, "error": "review repository unavailable"}

    try:
        base_ref, head_ref, merge_base = review_diff_range(
            base_sha=cfg.base_sha or "",
            head_sha=cfg.head_sha or "",
            last_reviewed_sha=cfg.last_reviewed_sha or "",
            re_review=bool(cfg.re_review),
        )
        sandbox_backend = get_cached_sandbox_backend(thread_id)
        work_dir = await resolve_sandbox_work_dir(sandbox_backend)
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
