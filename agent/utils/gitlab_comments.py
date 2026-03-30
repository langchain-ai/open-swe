"""GitLab note helpers."""

from __future__ import annotations

import hmac
import logging
from typing import Any
from urllib.parse import quote

import httpx

from .gitlab import get_gitlab_base_url

logger = logging.getLogger(__name__)

OPEN_SWE_TAGS = ("@openswe", "@open-swe", "@openswe-dev")


def verify_gitlab_webhook_secret(token: str, secret: str) -> bool:
    """Verify the GitLab webhook secret token."""
    if not secret:
        logger.warning("GITLAB_WEBHOOK_SECRET is not configured — rejecting webhook request")
        return False
    return hmac.compare_digest(token, secret)


async def post_gitlab_note(
    repo_config: dict[str, str],
    body: str,
    *,
    token: str,
    issue_iid: int | None = None,
    merge_request_iid: int | None = None,
    commit_sha: str | None = None,
) -> bool:
    """Post a note to a GitLab issue, merge request, or commit."""
    base_url = get_gitlab_base_url()
    if not base_url:
        logger.error("GITLAB_URL is not configured")
        return False

    owner = repo_config.get("owner", "")
    repo = repo_config.get("name", "")
    project_path = quote(f"{owner.strip('/')}/{repo.strip('/')}".strip("/"), safe="")
    payload_key = "body"
    if commit_sha:
        endpoint = f"{base_url}/api/v4/projects/{project_path}/repository/commits/{quote(commit_sha, safe='')}/comments"
        payload_key = "note"
    elif merge_request_iid:
        endpoint = f"{base_url}/api/v4/projects/{project_path}/merge_requests/{merge_request_iid}/notes"
    elif issue_iid:
        endpoint = f"{base_url}/api/v4/projects/{project_path}/issues/{issue_iid}/notes"
    else:
        logger.error(
            "No GitLab issue_iid, merge_request_iid, or commit_sha was provided for comment"
        )
        return False

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                endpoint,
                json={payload_key: body},
                headers={"PRIVATE-TOKEN": token, "Accept": "application/json"},
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError:
            logger.exception("Failed to post note to GitLab")
            return False


def extract_gitlab_repo_config(payload: dict[str, Any]) -> dict[str, str]:
    """Extract repo config from a GitLab webhook payload."""
    project = payload.get("project", {})
    path_with_namespace = str(project.get("path_with_namespace", "")).strip("/")
    if "/" in path_with_namespace:
        owner, name = path_with_namespace.rsplit("/", 1)
        return {"owner": owner, "name": name, "provider": "gitlab"}
    return {
        "owner": str(project.get("namespace", "")).strip("/"),
        "name": str(project.get("path", "")).strip("/"),
        "provider": "gitlab",
    }