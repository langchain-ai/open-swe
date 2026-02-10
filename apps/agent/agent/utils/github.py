"""GitHub API utilities."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# HTTP status codes
HTTP_CREATED = 201
HTTP_UNPROCESSABLE_ENTITY = 422


async def create_github_pr(
    repo_owner: str,
    repo_name: str,
    github_token: str,
    title: str,
    head_branch: str,
    base_branch: str,
    body: str,
) -> tuple[str | None, int | None]:
    """Create a GitHub pull request via the API.

    Args:
        repo_owner: Repository owner (e.g., "langchain-ai")
        repo_name: Repository name (e.g., "deepagents")
        github_token: GitHub access token
        title: PR title
        head_branch: Source branch name
        base_branch: Target branch name
        body: PR description

    Returns:
        Tuple of (pr_url, pr_number) if successful, (None, None) otherwise
    """
    pr_payload = {
        "title": title,
        "head": head_branch,
        "base": base_branch,
        "body": body,
    }

    logger.info(
        "Creating PR: head=%s, base=%s, repo=%s/%s",
        head_branch,
        base_branch,
        repo_owner,
        repo_name,
    )

    try:
        async with httpx.AsyncClient() as http_client:
            pr_response = await http_client.post(
                f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls",
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json=pr_payload,
            )

            pr_data = pr_response.json()

            if pr_response.status_code == HTTP_CREATED:
                pr_url = pr_data.get("html_url")
                pr_number = pr_data.get("number")
                logger.info("PR created successfully: %s", pr_url)
                return pr_url, pr_number

            if pr_response.status_code == HTTP_UNPROCESSABLE_ENTITY:
                logger.error("GitHub API validation error (422): %s", pr_data.get("message"))
            else:
                logger.error(
                    "GitHub API error (%s): %s",
                    pr_response.status_code,
                    pr_data.get("message"),
                )

            if "errors" in pr_data:
                logger.error("GitHub API errors detail: %s", pr_data.get("errors"))

            return None, None

    except httpx.HTTPError:
        logger.exception("Failed to create PR via GitHub API")
        return None, None


async def get_github_default_branch(
    repo_owner: str,
    repo_name: str,
    github_token: str,
) -> str:
    """Get the default branch of a GitHub repository via the API.

    Args:
        repo_owner: Repository owner (e.g., "langchain-ai")
        repo_name: Repository name (e.g., "deepagents")
        github_token: GitHub access token

    Returns:
        The default branch name (e.g., "main" or "master")
    """
    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(
                f"https://api.github.com/repos/{repo_owner}/{repo_name}",
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )

            if response.status_code == 200:  # noqa: PLR2004
                repo_data = response.json()
                default_branch = repo_data.get("default_branch", "main")
                logger.debug("Got default branch from GitHub API: %s", default_branch)
                return default_branch

            logger.warning(
                "Failed to get repo info from GitHub API (%s), falling back to 'main'",
                response.status_code,
            )
            return "main"

    except httpx.HTTPError:
        logger.exception("Failed to get default branch from GitHub API, falling back to 'main'")
        return "main"
