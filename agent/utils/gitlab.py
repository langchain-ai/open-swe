"""GitLab API helpers."""

from __future__ import annotations

import logging
import os
from urllib.parse import quote, urlparse

import httpx

logger = logging.getLogger(__name__)

HTTP_CREATED = 201
HTTP_BAD_REQUEST = 400
HTTP_CONFLICT = 409


def get_gitlab_base_url() -> str:
    """Return the configured GitLab base URL without the API suffix."""
    base_url = os.environ.get("GITLAB_URL", "").strip().rstrip("/")
    if base_url.endswith("/api/v4"):
        base_url = base_url[: -len("/api/v4")]
    return base_url


def _project_path(repo_owner: str, repo_name: str) -> str:
    owner = repo_owner.strip("/")
    name = repo_name.strip("/")
    return f"{owner}/{name}" if owner else name


def _project_api_path(repo_owner: str, repo_name: str) -> str:
    return quote(_project_path(repo_owner, repo_name), safe="")


def _gitlab_headers(token: str) -> dict[str, str]:
    return {
        "PRIVATE-TOKEN": token,
        "Accept": "application/json",
    }


async def get_gitlab_default_branch(
    repo_owner: str,
    repo_name: str,
    gitlab_token: str,
) -> str:
    """Get the default branch of a GitLab repository."""
    base_url = get_gitlab_base_url()
    if not base_url:
        logger.warning("GITLAB_URL not configured, falling back to 'main'")
        return "main"

    project_path = _project_api_path(repo_owner, repo_name)

    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(
                f"{base_url}/api/v4/projects/{project_path}",
                headers=_gitlab_headers(gitlab_token),
            )
            if response.status_code == 200:  # noqa: PLR2004
                return response.json().get("default_branch", "main")
            logger.warning(
                "Failed to get repo info from GitLab API (%s), falling back to 'main'",
                response.status_code,
            )
    except httpx.HTTPError:
        logger.exception("Failed to get default branch from GitLab API, falling back to 'main'")

    return "main"


async def create_gitlab_merge_request(
    repo_owner: str,
    repo_name: str,
    gitlab_token: str,
    title: str,
    head_branch: str,
    base_branch: str,
    body: str,
) -> tuple[str | None, int | None, bool]:
    """Create a GitLab merge request, or reuse an existing one for the source branch."""
    base_url = get_gitlab_base_url()
    if not base_url:
        logger.error("GITLAB_URL is not configured")
        return None, None, False

    project_path = _project_api_path(repo_owner, repo_name)
    payload = {
        "title": title,
        "source_branch": head_branch,
        "target_branch": base_branch,
        "description": body,
        "remove_source_branch": False,
    }

    logger.info(
        "Creating GitLab MR: source=%s, target=%s, repo=%s/%s",
        head_branch,
        base_branch,
        repo_owner,
        repo_name,
    )

    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.post(
                f"{base_url}/api/v4/projects/{project_path}/merge_requests",
                headers=_gitlab_headers(gitlab_token),
                json=payload,
            )

            response_data = response.json()
            if response.status_code == HTTP_CREATED:
                return response_data.get("web_url"), response_data.get("iid"), False

            if response.status_code in {HTTP_BAD_REQUEST, HTTP_CONFLICT}:
                existing = await _find_existing_gitlab_merge_request(
                    http_client=http_client,
                    repo_owner=repo_owner,
                    repo_name=repo_name,
                    gitlab_token=gitlab_token,
                    source_branch=head_branch,
                    target_branch=base_branch,
                )
                if existing:
                    return existing[0], existing[1], True

            logger.error(
                "GitLab API error (%s): %s",
                response.status_code,
                response_data,
            )
            return None, None, False
        except httpx.HTTPError:
            logger.exception("Failed to create merge request via GitLab API")
            return None, None, False


async def _find_existing_gitlab_merge_request(
    http_client: httpx.AsyncClient,
    repo_owner: str,
    repo_name: str,
    gitlab_token: str,
    source_branch: str,
    target_branch: str,
) -> tuple[str | None, int | None]:
    """Find an existing open GitLab merge request for the source branch."""
    base_url = get_gitlab_base_url()
    project_path = _project_api_path(repo_owner, repo_name)
    response = await http_client.get(
        f"{base_url}/api/v4/projects/{project_path}/merge_requests",
        headers=_gitlab_headers(gitlab_token),
        params={
            "state": "opened",
            "source_branch": source_branch,
            "target_branch": target_branch,
            "per_page": 1,
        },
    )
    if response.status_code != 200:  # noqa: PLR2004
        return None, None

    data = response.json()
    if not data:
        return None, None

    mr = data[0]
    return mr.get("web_url"), mr.get("iid")


def get_gitlab_host_url() -> str:
    """Return the host portion of the configured GitLab URL for git credentials."""
    base_url = get_gitlab_base_url()
    if not base_url:
        return ""

    parsed = urlparse(base_url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return base_url