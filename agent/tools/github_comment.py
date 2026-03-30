from typing import Any

from langgraph.config import get_config

from ..utils.github_app import get_github_app_installation_token
from ..utils.github_comments import post_github_comment


async def github_comment(message: str, issue_number: int) -> dict[str, Any]:
    """Post a comment to a GitHub issue or pull request."""
    config = get_config()
    configurable = config.get("configurable", {})

    repo_config = configurable.get("repo", {})
    if not issue_number:
        return {"success": False, "error": "Missing issue_number argument"}
    if not repo_config:
        return {"success": False, "error": "No repo config found in config"}
    if not message.strip():
        return {"success": False, "error": "Message cannot be empty"}

    token = await get_github_app_installation_token()
    if not token:
        return {"success": False, "error": "Failed to get GitHub App installation token"}

    success = await post_github_comment(repo_config, issue_number, message, token=token)
    return {"success": success}
