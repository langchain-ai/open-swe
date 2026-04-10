import logging
from typing import Any

import httpx

from ..utils.github_app import get_github_app_installation_token

logger = logging.getLogger(__name__)


async def list_repos(
    organization_name: str,
    page: int = 1,
    per_page: int = 100,
    sort: str = "updated",
    name_filter: str | None = None,
) -> dict[str, Any]:
    """List GitHub repositories for an organization via the GitHub API.

    Note: This uses the /orgs/{name}/repos endpoint, which only works for
    GitHub organizations, not personal user accounts.

    Args:
        organization_name: The GitHub organization to list repos for.
        page: Page number to fetch (default: 1).
        per_page: Number of repos per page, max 100 (default: 100).
        sort: Sort field — "updated", "created", "pushed", or "full_name" (default: "updated").
        name_filter: Optional substring to filter repo names by (case-insensitive).

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
                params={"per_page": min(per_page, 100), "sort": sort, "page": page},
                timeout=10,
            )
        if response.status_code == 200:
            repos: list[str] = [r["name"] for r in response.json()]
            if name_filter:
                repos = [r for r in repos if name_filter.lower() in r.lower()]

            has_next = 'rel="next"' in response.headers.get("link", "")
            result: dict[str, Any] = {"repos": repos, "page": page, "has_next_page": has_next}
            return result
        return {"error": f"GitHub API returned status {response.status_code}"}
    except Exception:
        logger.warning("Failed to fetch repos for org %s", organization_name)
        return {"error": f"Failed to fetch repos for org {organization_name}"}
