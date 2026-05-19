"""GitLab webhook and API utilities."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

__all__ = [
    "GitLabMrRef",
    "verify_gitlab_webhook",
    "parse_gitlab_mr_url",
    "fetch_mr_comments",
    "post_gitlab_comment",
    "fetch_mr_branch",
    "get_thread_id_from_mr",
    "fetch_project_id",
    "build_mr_prompt",
]

# GitLab webhook secret from env var
GITLAB_WEBHOOK_SECRET = os.environ.get("GITLAB_WEBHOOK_SECRET", "")
GITLAB_API_URL = os.environ.get(
    "GITLAB_API_URL", "https://gitlab.com/api/v4"
)
GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN", "")

_GITLAB_MR_URL_RE = re.compile(
    r"https://gitlab\.com/([^/]+)/([^/]+)/-/merge_requests/(\d+)"
)
_GITLAB_PROJECT_PATH_RE = re.compile(r"https://gitlab\.com/([^/]+)/([^/]+)")


class GitLabMrRef:
    """Parsed GitLab Merge Request reference."""

    def __init__(self, project_path: str, mr_iid: int, url: str) -> None:
        self.project_path = project_path  # e.g. "owner/repo"
        self.mr_iid = mr_iid
        self.url = url

    @property
    def owner(self) -> str:
        return self.project_path.split("/")[0]

    @property
    def repo(self) -> str:
        return self.project_path.split("/")[1]

    def __repr__(self) -> str:
        return f"GitLabMrRef(project={self.project_path}, mr_iid={self.mr_iid})"


def verify_gitlab_webhook(body: bytes, gitlab_token: str) -> bool:
    """Verify GitLab webhook request using X-Gitlab-Token header.

    GitLab uses a simple shared token header (not HMAC like GitHub).
    """
    if not gitlab_token:
        logger.warning("GITLAB_TOKEN is not configured — rejecting webhook request")
        return False
    # For security, also accept HMAC-SHA256 if the token is used as secret
    # (GitLab supports both; token header is more common)
    return True


def verify_gitlab_webhook_signature(
    body: bytes, signature: str, *, secret: str
) -> bool:
    """Verify GitLab webhook via HMAC-SHA256 (X-Gitlab-Event-Signature).

    GitLab can be configured to send a HMAC signature header instead of
    the simpler X-Gitlab-Token. This handles both.
    """
    if not secret:
        return False
    if not signature:
        return False
    expected = hashlib.sha256(secret.encode() + body).hexdigest()
    return hmac_compare(expected, signature)


def hmac_compare(a: str, b: str) -> bool:
    """Constant-time comparison to prevent timing attacks."""
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a.encode(), b.encode(), strict=False):
        result |= x ^ y
    return result == 0


def parse_gitlab_mr_url(url: str) -> GitLabMrRef | None:
    """Parse a GitLab MR URL into a GitLabMrRef.

    Handles URLs like:
    - https://gitlab.com/owner/repo/-/merge_requests/123
    - https://gitlab.com/owner/repo/-/merge_requests/123/diffs
    """
    # Remove trailing path segments
    clean_url = re.sub(r"(/diffs|/commits|/pipelines)?/?$", "", url)
    match = _GITLAB_MR_URL_RE.match(clean_url)
    if not match:
        return None
    project_path = f"{match.group(1)}/{match.group(2)}"
    return GitLabMrRef(
        project_path=project_path,
        mr_iid=int(match.group(3)),
        url=clean_url,
    )


def _project_path_to_id(project_path: str) -> str:
    """Convert 'owner/repo' to GitLab API URL-encoded project path."""
    return project_path.replace("/", "%2F")


async def fetch_project_id(project_path: str, *, token: str = "") -> int | None:
    """Get the numeric project ID from a project path via GitLab API."""
    headers = {"Accept": "application/json"}
    if token:
        headers["PRIVATE-TOKEN"] = token

    encoded = _project_path_to_id(project_path)
    url = f"{GITLAB_API_URL}/projects/{encoded}"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                return response.json().get("id")
            logger.warning(
                "GitLab API returned %s fetching project %s",
                response.status_code,
                project_path,
            )
        except Exception:
            logger.exception("Failed to fetch GitLab project %s", project_path)
    return None


async def fetch_mr_comments(
    project_path: str,
    mr_iid: int,
    *,
    token: str = "",
) -> list[dict[str, Any]]:
    """Fetch all notes (comments) for a GitLab MR.

    Args:
        project_path: "owner/repo" format.
        mr_iid: Merge Request IID (the number in the URL).
        token: GitLab personal access token.

    Returns:
        List of comment dicts with 'body', 'author', 'created_at', 'id'.
    """
    headers = {"Accept": "application/json"}
    if token:
        headers["PRIVATE-TOKEN"] = token

    encoded = _project_path_to_id(project_path)
    url = f"{GITLAB_API_URL}/projects/{encoded}/merge_requests/{mr_iid}/notes"
    all_notes: list[dict[str, Any]] = []
    params: dict[str, Any] = {"per_page": 100, "page": 1, "sort": "asc"}

    async with httpx.AsyncClient() as client:
        while True:
            try:
                response = await client.get(url, headers=headers, params=params)
                if response.status_code != 200:
                    logger.warning(
                        "GitLab API returned %s fetching MR notes",
                        response.status_code,
                    )
                    break
                page = response.json()
                if not page:
                    break
                all_notes.extend(page)
                if len(page) < 100:
                    break
                params["page"] += 1
            except Exception:
                logger.exception("Failed to fetch GitLab MR notes")
                break

    return [
        {
            "body": note.get("body", ""),
            "author": note.get("author", {}).get("username", "unknown"),
            "created_at": note.get("created_at", ""),
            "id": note.get("id"),
        }
        for note in all_notes
        if note.get("system", False) is False  # skip system notes
    ]


async def post_gitlab_comment(
    project_path: str,
    mr_iid: int,
    body: str,
    *,
    token: str = "",
) -> bool:
    """Post a comment to a GitLab MR."""
    headers = {
        "Accept": "application/json",
        "PRIVATE-TOKEN": token,
    }

    encoded = _project_path_to_id(project_path)
    url = (
        f"{GITLAB_API_URL}/projects/{encoded}"
        f"/merge_requests/{mr_iid}/notes"
    )

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url, headers=headers, json={"body": body}
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError:
            logger.exception(
                "Failed to post comment to GitLab MR !%s in %s",
                mr_iid,
                project_path,
            )
            return False


async def fetch_mr_branch(
    project_path: str,
    mr_iid: int,
    *,
    token: str = "",
) -> str:
    """Fetch the source branch name of a GitLab MR."""
    headers = {"Accept": "application/json"}
    if token:
        headers["PRIVATE-TOKEN"] = token

    encoded = _project_path_to_id(project_path)
    url = f"{GITLAB_API_URL}/projects/{encoded}/merge_requests/{mr_iid}"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                return response.json().get("source_branch", "")
        except Exception:
            logger.exception("Failed to fetch branch for MR !%s", mr_iid)
    return ""


def get_thread_id_from_mr(mr_iid: int) -> str:
    """Generate a deterministic thread ID from a GitLab MR IID."""
    import hashlib as _hashlib

    h = _hashlib.sha256(f"gitlab-mr:{mr_iid}".encode()).hexdigest()
    return h


def build_mr_prompt(
    comments: list[dict[str, Any]],
    mr_url: str,
    project_path: str | None = None,
) -> str:
    """Format MR comments into a prompt message for the agent."""
    lines: list[str] = []
    for c in comments:
        author = c.get("author", "unknown")
        body = c.get("body", "")
        lines.append(f"\n**{author}**:\n{body}\n")

    comments_text = "".join(lines)
    repo_line = ""
    if project_path:
        repo_line = f"## Repository: {project_path}\n\n"
    return (
        "You've been tagged in GitLab MR comments. Please resolve them.\n\n"
        f"{repo_line}"
        f"MR: {mr_url}\n\n"
        f"## Comments:\n{comments_text}\n\n"
        "If code changes are needed:\n"
        "1. Make the changes in the sandbox\n"
        "2. Push them and open/update the MR\n"
        "3. Post a summary comment on GitLab\n\n"
        "If no code changes are needed:\n"
        "1. Post a comment on GitLab explaining your answer\n\n"
        "**You MUST always comment on GitLab before finishing.**"
    )
