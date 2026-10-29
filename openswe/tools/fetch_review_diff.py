"""Tool: materialize the current reviewer diff in the sandbox."""

import re
from typing import Annotated

from langgraph.prebuilt import InjectedState

from openswe.review.diff import changed_files, materialize_review_diff, review_diff_range
from openswe.run_config import RunConfig
from openswe.runtime import get_cached_sandbox_backend
from openswe.sandboxes.paths import resolve_checkout_dir, resolve_sandbox_work_dir

_MAX_CHANGED_FILES = 200
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


async def fetch_review_diff(
    work_dir: Annotated[str | None, InjectedState("work_dir")] = None,
) -> dict[str, object]:
    """Implement the `fetch_review_diff` tool."""
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
        await sandbox_backend.ready()
        work_dir = work_dir or await resolve_sandbox_work_dir(sandbox_backend)
        repo_dir = await resolve_checkout_dir(sandbox_backend, work_dir, repo_name)
        materialized = await materialize_review_diff(
            sandbox_backend,
            work_dir=repo_dir,
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
