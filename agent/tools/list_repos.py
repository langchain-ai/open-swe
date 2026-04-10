import logging
from typing import Any

import httpx

from ..utils.github_app import get_github_app_installation_token

logger = logging.getLogger(__name__)


async def list_repos(organization_name: str) -> dict[str, Any]:
    """List GitHub repositories for an organization via the GitHub API.

    Args:
        organization_name: The GitHub organization to list repos for.

    If unsure which repo to use, ask the user for confirmation.
    """
    try:
        headers = {"Accept": "application/vnd.github+json"}
        token = await get_github_app_installation_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.github.com/orgs/{organization_name}/repos",
                headers=headers,
                params={"per_page": 100, "sort": "updated"},
                timeout=10,
            )
        if response.status_code == 200:
            repos = [{"owner": organization_name, "name": r["name"]} for r in response.json()]
            return {"repos": repos}
        return {"error": f"GitHub API returned status {response.status_code}"}
    except Exception:
        logger.warning("Failed to fetch repos for org %s", organization_name)
        return {"error": f"Failed to fetch repos for org {organization_name}"}
