"""Tool: ``publish_pr_tldr``. Upsert a reviewer-focused TLDR comment on a PR."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from langgraph.config import get_config

from ..reviewer_findings import (
    ReviewerThreadMissingError,
    get_thread_id_from_runtime,
    get_thread_metadata,
    resolve_review_head_sha,
    set_reviewer_thread_metadata,
    thread_missing_tool_result,
)
from ..reviewer_publish import render_pr_tldr_comment, upsert_pr_tldr_comment
from ..utils.github_token import (
    GitHubAuthError,
    get_github_token,
    invalidate_cached_github_token,
)

# Keep the TLDR short enough to scan in the sidebar; longer text defeats the
# point of a TLDR and is rejected so the agent re-summarizes instead.
MAX_TLDR_CHARS = 4000


def publish_pr_tldr(tldr: str) -> dict[str, Any]:
    """Post (or update) the PR's reviewer-focused TLDR comment.

    Call this once at the end of the run with a concise, decision-oriented
    summary of what a reviewer needs to evaluate the PR (data-model/API changes,
    new assumptions, caching/concurrency/auth decisions, notable tradeoffs).
    Skip restating variable names or a file-by-file walkthrough. The comment is
    upserted in place, so each run keeps a single always-current TLDR.

    Args:
        tldr: Markdown body of the TLDR (a 1-line headline plus a few bullets).

    Returns:
        Dictionary with ``success`` and, on success, ``comment_id``.
    """
    text = tldr.strip() if isinstance(tldr, str) else ""
    if not text:
        return {"success": False, "error": "tldr must be a non-empty string"}
    if len(text) > MAX_TLDR_CHARS:
        return {
            "success": False,
            "error": f"tldr too long ({len(text)} chars); keep it under {MAX_TLDR_CHARS}",
        }

    config = get_config()
    raw_configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    configurable = raw_configurable if isinstance(raw_configurable, dict) else {}
    repo_config = configurable.get("repo")
    pr_number = configurable.get("pr_number")

    if (
        not isinstance(repo_config, dict)
        or not repo_config.get("owner")
        or not repo_config.get("name")
    ):
        return {"success": False, "error": "Missing repo info in run config"}
    if not isinstance(pr_number, int):
        return {"success": False, "error": "Missing pr_number in run config"}

    if configurable.get("reviewer_eval") is True or configurable.get("eval") is True:
        return {"success": True, "dry_run": True, "comment_id": None}

    token = get_github_token()
    if not token:
        return {"success": False, "error": "No GitHub token available"}

    try:
        return asyncio.run(
            _publish_pr_tldr_async(
                owner=str(repo_config["owner"]),
                repo=str(repo_config["name"]),
                pr_number=pr_number,
                tldr=text,
                token=token,
                configurable=configurable,
            )
        )
    except ReviewerThreadMissingError as exc:
        return thread_missing_tool_result(exc)
    except GitHubAuthError as exc:
        thread_id = get_thread_id_from_runtime()
        if thread_id:
            asyncio.run(invalidate_cached_github_token(thread_id))
        return {
            "success": False,
            "error": "GitHub returned 401 — the cached token is invalid or revoked.",
            "auth_error": str(exc),
        }


async def _publish_pr_tldr_async(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    tldr: str,
    token: str,
    configurable: dict[str, Any],
) -> dict[str, Any]:
    thread_id = get_thread_id_from_runtime()
    metadata = await get_thread_metadata(thread_id)
    existing = metadata.get("pr_tldr")
    existing_comment_id = existing.get("comment_id") if isinstance(existing, dict) else None
    body = render_pr_tldr_comment(markdown=tldr, pr_number=pr_number, thread_id=thread_id)
    comment_id = await upsert_pr_tldr_comment(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        body=body,
        token=token,
        existing_comment_id=existing_comment_id if isinstance(existing_comment_id, int) else None,
    )
    if comment_id is None:
        return {"success": False, "error": "Failed to post TLDR comment to GitHub"}
    head_sha = await resolve_review_head_sha(thread_id, configurable)
    await set_reviewer_thread_metadata(
        thread_id,
        extra={
            "pr_tldr": {
                "markdown": tldr,
                "comment_id": comment_id,
                "head_sha": head_sha,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        },
    )
    return {"success": True, "comment_id": comment_id}
