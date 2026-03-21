import asyncio
import os
from typing import Any

from langgraph.config import get_config

from ..utils.git_provider import GITLAB, get_git_provider
from ..utils.github_app import get_github_app_installation_token
from ..utils.github_comments import post_github_comment
from ..utils.gitlab import post_gitlab_note


def github_comment(message: str, issue_number: int) -> dict[str, Any]:
    """Post a comment to a GitHub issue/PR or GitLab issue/MR."""
    config = get_config()
    configurable = config.get("configurable", {})

    repo_config = configurable.get("repo", {})
    if not issue_number:
        return {"success": False, "error": "Missing issue_number argument"}
    if not repo_config:
        return {"success": False, "error": "No repo config found in config"}
    if not message.strip():
        return {"success": False, "error": "Message cannot be empty"}

    provider = get_git_provider()

    if provider == GITLAB:
        gitlab_token = os.environ.get("GITLAB_TOKEN", "")
        if not gitlab_token:
            return {"success": False, "error": "GITLAB_TOKEN not configured"}
        owner = repo_config.get("owner", "")
        name = repo_config.get("name", "")
        success = asyncio.run(
            post_gitlab_note(owner, name, gitlab_token, issue_number, message)
        )
        return {"success": success}

    token = asyncio.run(get_github_app_installation_token())
    if not token:
        return {"success": False, "error": "Failed to get GitHub App installation token"}

    success = asyncio.run(post_github_comment(repo_config, issue_number, message, token=token))
    return {"success": success}
