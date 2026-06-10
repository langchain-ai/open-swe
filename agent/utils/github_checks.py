"""GitHub Checks API helpers for the reviewer's PR check run.

A check run named ``Open SWE Review`` is created on the PR head SHA when an
auto-review is dispatched, so the PR's checks section shows the review as
in-progress. ``publish_review`` (or the after-agent fallback) completes it.

All calls are best-effort: check runs require the GitHub App's
``Checks: Read & write`` permission, and a missing permission must never
break review dispatch or publish.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

REVIEW_CHECK_RUN_NAME = "Open SWE Review"

_GITHUB_API_BASE = "https://api.github.com"

CheckConclusion = Literal["success", "neutral", "failure"]


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def create_review_check_run(
    *,
    owner: str,
    repo: str,
    head_sha: str,
    token: str,
    details_url: str | None = None,
) -> int | None:
    """Create an in-progress ``Open SWE Review`` check run on ``head_sha``.

    Returns the check run id, or ``None`` on any failure (most commonly the
    App lacking the Checks permission).
    """
    payload: dict[str, object] = {
        "name": REVIEW_CHECK_RUN_NAME,
        "head_sha": head_sha,
        "status": "in_progress",
        "started_at": _utc_now_iso(),
        "output": {
            "title": "Review in progress",
            "summary": "Open SWE is reviewing this pull request…",
        },
    }
    if details_url:
        payload["details_url"] = details_url
    url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/check-runs"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url, headers=_github_headers(token), json=payload, timeout=30
            )
            response.raise_for_status()
    except httpx.HTTPError:
        logger.exception(
            "Failed to create review check run for %s/%s@%s "
            "(does the GitHub App have Checks: Read & write?)",
            owner,
            repo,
            head_sha,
        )
        return None
    data = response.json()
    check_run_id = data.get("id") if isinstance(data, dict) else None
    return check_run_id if isinstance(check_run_id, int) else None


async def complete_review_check_run(
    *,
    owner: str,
    repo: str,
    check_run_id: int,
    token: str,
    conclusion: CheckConclusion,
    title: str,
    summary: str,
) -> bool:
    """Mark a review check run as completed. Returns True on success."""
    url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/check-runs/{check_run_id}"
    payload = {
        "status": "completed",
        "conclusion": conclusion,
        "completed_at": _utc_now_iso(),
        "output": {"title": title, "summary": summary},
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                url, headers=_github_headers(token), json=payload, timeout=30
            )
            response.raise_for_status()
    except httpx.HTTPError:
        logger.exception(
            "Failed to complete review check run %s on %s/%s", check_run_id, owner, repo
        )
        return False
    return True


def review_check_conclusion(surfaced_count: int) -> tuple[CheckConclusion, str, str]:
    """Map a publish result to (conclusion, title, summary).

    Always ``success`` so the check is informational and non-blocking, and so
    GitHub groups it under "successful checks" rather than a confusing
    "neutral check". The finding count is surfaced in the title; the findings
    themselves are posted as PR comments.
    """
    if surfaced_count > 0:
        issue_word = "issue" if surfaced_count == 1 else "issues"
        return (
            "success",
            f"Found {surfaced_count} potential {issue_word}",
            f"Open SWE surfaced {surfaced_count} potential {issue_word} on this pull request.",
        )
    return (
        "success",
        "No issues found",
        "Open SWE reviewed this pull request and found no issues.",
    )
