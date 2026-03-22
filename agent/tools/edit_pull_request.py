import asyncio
import logging
from typing import Any

from langgraph.config import get_config

from ..utils.github import edit_github_pr
from ..utils.github_token import get_github_token

logger = logging.getLogger(__name__)


def edit_pull_request(
    pr_number: int,
    title: str | None = None,
    body: str | None = None,
) -> dict[str, Any]:
    """Edit the title and/or body of an existing GitHub Pull Request.

    Use this tool to update a PR's title or description after it has been created.
    At least one of `title` or `body` must be provided.

    Args:
        pr_number: The pull request number to edit.
        title: New title for the PR. If not provided, the title is left unchanged.
        body: New body/description for the PR. If not provided, the body is left unchanged.

    Returns:
        Dictionary containing:
        - success: Whether the operation completed successfully
        - error: Error string if something failed, otherwise None
        - pr_url: URL of the updated PR if successful, otherwise None
    """
    try:
        config = get_config()
        configurable = config.get("configurable", {})

        repo_config = configurable.get("repo", {})
        repo_owner = repo_config.get("owner")
        repo_name = repo_config.get("name")
        if not repo_owner or not repo_name:
            return {
                "success": False,
                "error": "Missing repo owner/name in config",
                "pr_url": None,
            }

        if not pr_number:
            return {"success": False, "error": "Missing pr_number argument", "pr_url": None}

        if title is None and body is None:
            return {
                "success": False,
                "error": "At least one of title or body must be provided",
                "pr_url": None,
            }

        github_token = get_github_token()
        if not github_token:
            return {"success": False, "error": "Missing GitHub token", "pr_url": None}

        pr_url, _pr_number = asyncio.run(
            edit_github_pr(
                repo_owner=repo_owner,
                repo_name=repo_name,
                github_token=github_token,
                pr_number=pr_number,
                title=title,
                body=body,
            )
        )

        if not pr_url:
            return {
                "success": False,
                "error": f"Failed to update PR #{pr_number}",
                "pr_url": None,
            }

        return {"success": True, "error": None, "pr_url": pr_url}
    except Exception as e:
        logger.exception("edit_pull_request failed")
        return {"success": False, "error": f"{type(e).__name__}: {e}", "pr_url": None}
