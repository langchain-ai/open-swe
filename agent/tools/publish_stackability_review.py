"""Publish the current stackability artifact as a non-blocking PR comment."""

from __future__ import annotations

from typing import Any

from langgraph.config import get_config

from ..review.findings import (
    ReviewerThreadMissingError,
    resolve_review_head_sha,
    thread_missing_tool_result,
)
from ..review.publish import post_status_comment, update_status_comment
from ..review.stackability import (
    STACKABILITY_REVIEW_MARKER,
    get_stackability_review,
    render_stackability_advisory,
    update_stackability_review,
)
from ..utils.github_comments import fetch_issue_comments
from ..utils.github_token import get_github_token


async def publish_stackability_review() -> dict[str, Any]:
    """Post the recorded stackability advisory once for the current artifact."""
    config = get_config()
    raw_configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    configurable = raw_configurable if isinstance(raw_configurable, dict) else {}
    thread_id = configurable.get("thread_id")
    repo = configurable.get("repo")
    pr_number = configurable.get("pr_number")
    if not isinstance(thread_id, str) or not thread_id:
        return {"success": False, "error": "reviewer_thread_unavailable"}
    if not isinstance(repo, dict) or not repo.get("owner") or not repo.get("name"):
        return {"success": False, "error": "missing_repo_config"}
    if not isinstance(pr_number, int):
        return {"success": False, "error": "missing_pr_number"}

    token = get_github_token()
    if not token:
        return {"success": False, "error": "github_token_unavailable"}

    try:
        artifact = await get_stackability_review(thread_id)
        if artifact is None:
            return {"success": False, "error": "stackability_review_unavailable"}
        publication = artifact["publication"]
        existing_id = publication.get("github_comment_id")
        if publication.get("state") == "published" and isinstance(existing_id, int):
            return {"success": True, "github_comment_id": existing_id, "already_published": True}

        live_head = await resolve_review_head_sha(thread_id, configurable)
        if not live_head:
            return {"success": False, "error": "review_head_unavailable"}
        if artifact["reviewed_head_sha"] != live_head:
            return {
                "success": False,
                "error": "stale_stackability_review",
                "reviewed_head_sha": artifact["reviewed_head_sha"],
                "live_head_sha": live_head,
            }

        owner = str(repo["owner"])
        repo_name = str(repo["name"])
        body = render_stackability_advisory(artifact)
        comments = await fetch_issue_comments(repo, pr_number, token=token)
        prior_comment_id = next(
            (
                comment.get("comment_id")
                for comment in comments
                if STACKABILITY_REVIEW_MARKER in str(comment.get("body", ""))
                and isinstance(comment.get("comment_id"), int)
            ),
            None,
        )
        if isinstance(prior_comment_id, int):
            updated = await update_status_comment(
                owner=owner,
                repo=repo_name,
                comment_id=prior_comment_id,
                body=body,
                token=token,
            )
            comment_id = prior_comment_id if updated else None
        else:
            comment_id = await post_status_comment(
                owner=owner,
                repo=repo_name,
                pr_number=pr_number,
                body=body,
                token=token,
            )
        if comment_id is None:
            return {"success": False, "error": "stackability_publication_failed"}
        await update_stackability_review(
            thread_id,
            publication={
                **publication,
                "mode": "manual_advisory",
                "state": "published",
                "github_comment_id": comment_id,
            },
        )
        return {"success": True, "github_comment_id": comment_id, "already_published": False}
    except ReviewerThreadMissingError as exc:
        return thread_missing_tool_result(exc)
