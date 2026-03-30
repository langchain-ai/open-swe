import asyncio
import os
from typing import Any

from langgraph.config import get_config

from ..utils.github_token import get_github_token
from ..utils.gitlab_comments import post_gitlab_note


def gitlab_comment(
    message: str,
    issue_iid: int | None = None,
    merge_request_iid: int | None = None,
    commit_sha: str | None = None,
) -> dict[str, Any]:
    """Post a comment to a GitLab issue, merge request, or commit."""
    config = get_config()
    configurable = config.get("configurable", {})

    repo_config = configurable.get("repo", {})
    if not repo_config:
        return {"success": False, "error": "No repo config found in config"}
    if not message.strip():
        return {"success": False, "error": "Message cannot be empty"}

    gitlab_issue = configurable.get("gitlab_issue", {})
    gitlab_merge_request = configurable.get("gitlab_merge_request", {})
    gitlab_commit = configurable.get("gitlab_commit", {})
    resolved_issue_iid = issue_iid or gitlab_issue.get("iid")
    resolved_merge_request_iid = merge_request_iid or gitlab_merge_request.get("iid")
    resolved_commit_sha = commit_sha or gitlab_commit.get("sha")

    token = get_github_token() or os.environ.get("GITLAB_TOKEN", "").strip()
    if not token:
        return {"success": False, "error": "Failed to get GitLab token"}

    success = asyncio.run(
        post_gitlab_note(
            repo_config,
            message,
            token=token,
            issue_iid=resolved_issue_iid,
            merge_request_iid=resolved_merge_request_iid,
            commit_sha=resolved_commit_sha,
        )
    )
    return {"success": success}