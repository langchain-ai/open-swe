"""Approval criteria from a repository's ``APPROVALS.md`` and each repository's approval mode.

The file supplies the criteria; the mode on the repository's review style record
decides whether a positive assessment only comments (``dry_run``) or submits a
GitHub approval (``approve``). Callers read the file at the pull request's base
commit so a pull request cannot rewrite the policy it is judged by.
"""

import logging

import httpx2

from agent.github.http import GITHUB_API_BASE, github_client, github_request
from agent.review.styles import REVIEW_STYLES, ApprovalMode, effective_approval_mode

logger = logging.getLogger(__name__)

APPROVALS_FILENAME = "APPROVALS.md"
APPROVALS_MAX_CHARS = 10_000


async def fetch_approvals_md(
    owner: str, repo: str, ref: str | None, *, token: str | None
) -> str | None:
    """``APPROVALS.md`` at ``ref`` (the default branch when ``None``).

    ``None`` when the file is absent, empty, too large, or unreadable.
    """
    if not owner or not repo:
        return None
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{APPROVALS_FILENAME}"
    extra = {"repository": f"{owner}/{repo}", "ref": ref or ""}
    try:
        async with github_client(
            token=token, headers={"Accept": "application/vnd.github.raw"}
        ) as client:
            response = await github_request(
                client, "GET", url, params={"ref": ref} if ref else None
            )
    except httpx2.HTTPError:
        logger.exception("approvals file fetch failed", extra=extra)
        return None
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        logger.warning(
            "approvals file fetch returned an unexpected status",
            extra={**extra, "status_code": response.status_code},
        )
        return None
    content = response.text.strip()
    if len(content) > APPROVALS_MAX_CHARS:
        logger.warning(
            "approvals file exceeds the size cap; ignoring it",
            extra={**extra, "chars": len(content), "max_chars": APPROVALS_MAX_CHARS},
        )
        return None
    return content or None


async def approval_mode_for(owner: str, repo: str) -> ApprovalMode:
    """The repository's approval mode; a failed lookup is ``dry_run``, which never approves."""
    try:
        record = await REVIEW_STYLES.get(f"{owner}/{repo}")
    except Exception:
        logger.exception("approval mode lookup failed", extra={"repository": f"{owner}/{repo}"})
        return "dry_run"
    return effective_approval_mode(record)


async def approval_policy_for_review(
    owner: str, repo: str, base_sha: str, *, token: str | None
) -> str | None:
    """The criteria a review of a pull request into ``base_sha`` assesses against, if any."""
    if not base_sha or await approval_mode_for(owner, repo) == "off":
        return None
    return await fetch_approvals_md(owner, repo, base_sha, token=token)
