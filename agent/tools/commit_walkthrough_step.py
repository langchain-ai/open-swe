"""Tool: ``commit_walkthrough_step``. Commits the staged changes as one step of the review walkthrough."""

import logging
from typing import Any

from agent.review_scout.git import ScoutGitError, commit_staged, committed_kinds
from agent.review_scout.paths import scout_repo_dir
from agent.run_config import RunConfig
from agent.runtime import get_cached_sandbox_backend

logger = logging.getLogger(__name__)

MAX_TITLE_CHARS = 120
MAX_SUMMARY_CHARS = 1_200


async def commit_walkthrough_step(title: str, summary: str, other: bool = False) -> dict[str, Any]:
    """Implement the `commit_walkthrough_step` tool."""
    trimmed_title = " ".join(title.split())[:MAX_TITLE_CHARS]
    if not trimmed_title:
        return {"success": False, "error": "title must name the step"}
    cfg = RunConfig.from_runtime()
    if not cfg.thread_id or not cfg.base_sha or not cfg.head_sha:
        return {"success": False, "error": "scout thread unavailable"}
    backend = get_cached_sandbox_backend(cfg.thread_id)
    repo_dir = await scout_repo_dir(backend, cfg)
    if repo_dir is None:
        return {"success": False, "error": "scout repository unavailable"}
    try:
        if not other and not any(
            await committed_kinds(backend, repo_dir, base_sha=cfg.base_sha, head_sha=cfg.head_sha)
        ):
            return {
                "success": False,
                "error": "commit the `other: true` pass first: stage every change a reviewer "
                "does not need to read and commit it before any step",
            }
        sha = await commit_staged(
            backend,
            repo_dir,
            title=trimmed_title,
            summary=summary.strip()[:MAX_SUMMARY_CHARS],
            other=other,
        )
    except ScoutGitError as exc:
        logger.warning("Review scout commit failed", extra={"scout_error": str(exc)})
        return {"success": False, "error": str(exc)}
    if sha is None:
        return {"success": False, "error": "nothing is staged; stage this step's changes first"}
    return {"success": True, "commit": sha}
