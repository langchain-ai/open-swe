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
import os
from datetime import UTC, datetime
from typing import Literal

import httpx

from .github_http import (
    GITHUB_API_BASE,
    github_client,
    github_headers,  # re-exported for backward compatibility
    github_request,
)

__all__ = ["github_headers"]

logger = logging.getLogger(__name__)

REVIEW_CHECK_RUN_NAME = "Open SWE Review"
AUTOFIX_CHECK_RUN_NAME = "Open SWE Auto-fix"

_GITHUB_API_BASE = GITHUB_API_BASE

CheckConclusion = Literal["success", "neutral", "failure"]


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
        async with github_client(token=token) as client:
            response = await github_request(client, "POST", url, json=payload)
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
        async with github_client(token=token) as client:
            response = await github_request(client, "PATCH", url, json=payload)
            response.raise_for_status()
    except httpx.HTTPError:
        logger.exception(
            "Failed to complete review check run %s on %s/%s", check_run_id, owner, repo
        )
        return False
    return True


async def post_autofix_status_check(
    *,
    owner: str,
    repo: str,
    head_sha: str,
    token: str,
    title: str,
    summary: str,
    details_url: str | None = None,
) -> bool:
    """Post an informational, completed ``Open SWE Auto-fix`` check on ``head_sha``.

    Completed immediately as ``neutral`` so it's non-blocking and never leaves a
    dangling in-progress check that could gate branch protection. Used as the
    auto-fix status channel instead of a PR comment (PR comments can trigger
    ``issue_comment`` automation like Atlantis/Terraform).
    """
    payload: dict[str, object] = {
        "name": AUTOFIX_CHECK_RUN_NAME,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": "neutral",
        "started_at": _utc_now_iso(),
        "completed_at": _utc_now_iso(),
        "output": {"title": title, "summary": summary},
    }
    if details_url:
        payload["details_url"] = details_url
    url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/check-runs"
    try:
        async with github_client(token=token) as client:
            response = await github_request(client, "POST", url, json=payload)
            response.raise_for_status()
    except httpx.HTTPError:
        logger.warning("Failed to post auto-fix status check for %s/%s@%s", owner, repo, head_sha)
        return False
    return True


def review_check_blocking_enabled() -> bool:
    """Return whether surfaced findings should fail the review check.

    Deliberately env-scoped (deployment-level merge policy) rather than a team
    setting: whoever operates branch protection owns this toggle.
    """
    return os.getenv("REVIEW_CHECK_BLOCKING", "").lower() in {"1", "true", "yes"}


def incomplete_review_check_result() -> tuple[CheckConclusion, str, str]:
    """Conclusion for a review run that ended without publishing.

    Shared by every settle path (after-agent middleware and the run-completion
    handler) so an unfinished review cannot satisfy a blocking check through
    one path while failing it through another.
    """
    return (
        "failure" if review_check_blocking_enabled() else "neutral",
        "Review did not complete",
        "The Open SWE review run ended without publishing a review. "
        "Re-trigger the review by pushing a commit or re-requesting it.",
    )


def review_check_conclusion(surfaced_count: int) -> tuple[CheckConclusion, str, str]:
    """Map a publish result to (conclusion, title, summary)."""
    if surfaced_count > 0:
        issue_word = "issue" if surfaced_count == 1 else "issues"
        return (
            "failure" if review_check_blocking_enabled() else "success",
            f"Found {surfaced_count} potential {issue_word}",
            f"Open SWE surfaced {surfaced_count} potential {issue_word} on this pull request.",
        )
    return (
        "success",
        "No issues found",
        "Open SWE reviewed this pull request and found no issues.",
    )
