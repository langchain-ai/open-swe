"""GitLab API utilities for merge requests, branches, and comments."""

from __future__ import annotations

import logging
import os

import httpx

from .git_provider import get_gitlab_host, get_gitlab_project_path

logger = logging.getLogger(__name__)

HTTP_CREATED = 201
HTTP_CONFLICT = 409


def _gitlab_api_base() -> str:
    """Build the GitLab API base URL."""
    host = get_gitlab_host()
    return f"https://{host}/api/v4"


def _gitlab_headers(token: str) -> dict[str, str]:
    """Build GitLab API request headers."""
    return {
        "PRIVATE-TOKEN": token,
        "Content-Type": "application/json",
    }


def _get_gitlab_token() -> str | None:
    """Get the GitLab token from env."""
    return os.environ.get("GITLAB_TOKEN")


async def create_gitlab_mr(
    repo_owner: str,
    repo_name: str,
    gitlab_token: str,
    title: str,
    head_branch: str,
    base_branch: str,
    body: str,
) -> tuple[str | None, int | None, bool]:
    """Create a GitLab merge request.

    Returns:
        Tuple of (mr_url, mr_iid, mr_existing) if successful, (None, None, False) otherwise.
    """
    api_base = _gitlab_api_base()
    project_path = get_gitlab_project_path(repo_owner, repo_name)

    mr_payload = {
        "title": title,
        "source_branch": head_branch,
        "target_branch": base_branch,
        "description": body,
    }

    logger.info(
        "Creating MR: source=%s, target=%s, project=%s/%s",
        head_branch,
        base_branch,
        repo_owner,
        repo_name,
    )

    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.post(
                f"{api_base}/projects/{project_path}/merge_requests",
                headers=_gitlab_headers(gitlab_token),
                json=mr_payload,
            )

            mr_data = response.json()

            if response.status_code == HTTP_CREATED:
                mr_url = mr_data.get("web_url")
                mr_iid = mr_data.get("iid")
                logger.info("MR created successfully: %s", mr_url)
                return mr_url, mr_iid, False

            if response.status_code == HTTP_CONFLICT:
                logger.info("MR already exists, searching for existing MR")
                existing = await _find_existing_mr(
                    http_client=http_client,
                    project_path=project_path,
                    gitlab_token=gitlab_token,
                    head_branch=head_branch,
                )
                if existing:
                    logger.info("Using existing MR: %s", existing[0])
                    return existing[0], existing[1], True

            logger.error(
                "GitLab API error (%s): %s",
                response.status_code,
                mr_data.get("message", mr_data),
            )
            return None, None, False

        except httpx.HTTPError:
            logger.exception("Failed to create MR via GitLab API")
            return None, None, False


async def _find_existing_mr(
    http_client: httpx.AsyncClient,
    project_path: str,
    gitlab_token: str,
    head_branch: str,
) -> tuple[str | None, int | None]:
    """Find an existing MR for the given source branch."""
    api_base = _gitlab_api_base()
    for state in ("opened", "all"):
        try:
            response = await http_client.get(
                f"{api_base}/projects/{project_path}/merge_requests",
                headers=_gitlab_headers(gitlab_token),
                params={"source_branch": head_branch, "state": state, "per_page": 1},
            )
            if response.status_code != 200:  # noqa: PLR2004
                continue
            data = response.json()
            if not data:
                continue
            mr = data[0]
            return mr.get("web_url"), mr.get("iid")
        except httpx.HTTPError:
            logger.exception("Failed to search for existing MR")
    return None, None


async def get_gitlab_default_branch(
    repo_owner: str,
    repo_name: str,
    gitlab_token: str,
) -> str:
    """Get the default branch of a GitLab project."""
    api_base = _gitlab_api_base()
    project_path = get_gitlab_project_path(repo_owner, repo_name)

    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(
                f"{api_base}/projects/{project_path}",
                headers=_gitlab_headers(gitlab_token),
            )

            if response.status_code == 200:  # noqa: PLR2004
                project_data = response.json()
                default_branch = project_data.get("default_branch", "main")
                logger.debug("Got default branch from GitLab API: %s", default_branch)
                return default_branch

            logger.warning(
                "Failed to get project info from GitLab API (%s), falling back to 'main'",
                response.status_code,
            )
            return "main"

    except httpx.HTTPError:
        logger.exception("Failed to get default branch from GitLab API, falling back to 'main'")
        return "main"


async def fetch_gitlab_issue_notes(
    repo_owner: str,
    repo_name: str,
    gitlab_token: str,
    issue_iid: int,
) -> list[dict]:
    """Fetch notes (comments) for a GitLab issue.

    Returns:
        List of note dicts with 'id', 'body', 'author', 'created_at' fields.
    """
    api_base = _gitlab_api_base()
    project_path = get_gitlab_project_path(repo_owner, repo_name)

    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(
                f"{api_base}/projects/{project_path}/issues/{issue_iid}/notes",
                headers=_gitlab_headers(gitlab_token),
                params={"per_page": 100, "sort": "asc"},
            )
            if response.status_code == 200:  # noqa: PLR2004
                return response.json()
            logger.warning(
                "Failed to fetch GitLab issue notes (%s)", response.status_code
            )
            return []
    except httpx.HTTPError:
        logger.exception("Failed to fetch GitLab issue notes")
        return []


async def post_gitlab_note(
    repo_owner: str,
    repo_name: str,
    gitlab_token: str,
    issue_iid: int,
    body: str,
    *,
    note_type: str = "issues",
) -> bool:
    """Post a note (comment) on a GitLab issue or merge request.

    Args:
        note_type: 'issues' or 'merge_requests'
    """
    api_base = _gitlab_api_base()
    project_path = get_gitlab_project_path(repo_owner, repo_name)

    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.post(
                f"{api_base}/projects/{project_path}/{note_type}/{issue_iid}/notes",
                headers=_gitlab_headers(gitlab_token),
                json={"body": body},
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError:
            logger.exception(
                "Failed to post note to GitLab %s #%s", note_type, issue_iid
            )
            return False
