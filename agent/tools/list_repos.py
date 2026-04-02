import os
from typing import Any

import httpx

from ..utils.linear_team_repo_map import LINEAR_TEAM_TO_REPO

DEFAULT_GITHUB_ORG = os.getenv("DEFAULT_GITHUB_ORG", "langchain-ai")


def _get_common_repos() -> list[dict[str, str]]:
    """Extract unique repos from LINEAR_TEAM_TO_REPO."""
    repos: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for team_config in LINEAR_TEAM_TO_REPO.values():
        entries: list[dict[str, str]] = []
        if "owner" in team_config and "name" in team_config:
            entries.append({"owner": team_config["owner"], "name": team_config["name"]})
        if "projects" in team_config:
            entries.extend(team_config["projects"].values())
        if "default" in team_config:
            entries.append(team_config["default"])

        for entry in entries:
            key = (entry["owner"], entry["name"])
            if key not in seen:
                seen.add(key)
                repos.append({"owner": entry["owner"], "name": entry["name"]})

    return repos


def list_repos(org: str | None = None) -> dict[str, Any]:
    """List available GitHub repositories.

    Returns common repos from the configured repo map.
    Pass org to also search that GitHub org via the API.
    If unsure which repo to use, ask the user for confirmation.
    """
    common_repos = _get_common_repos()
    result: dict[str, Any] = {
        "common_repos": common_repos,
        "default_org": DEFAULT_GITHUB_ORG,
    }

    if org:
        try:
            response = httpx.get(
                f"https://api.github.com/orgs/{org}/repos",
                headers={"Accept": "application/vnd.github+json"},
                params={"per_page": 100, "sort": "updated"},
                timeout=10,
            )
            if response.status_code == 200:
                result["org_repos"] = [{"owner": org, "name": r["name"]} for r in response.json()]
        except Exception:
            pass

    return result
