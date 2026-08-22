"""GitHub CI read helpers for auto-fixing failing checks on agent PRs.

These read third-party CI results (GitHub Actions check runs, the legacy
combined commit status) so the auto-fix flow can detect failures, dedupe per
commit, and decide whether a failure is pre-existing on the base branch.

All calls are best-effort: they require the GitHub App's ``Checks: Read``
permission, and a missing permission or transient error must never break
webhook handling.
"""

import logging
from typing import Any

import httpx

from .api import github_client, github_paginate, github_request, github_url
from .checks import REVIEW_CHECK_RUN_NAME

logger = logging.getLogger(__name__)

# Check-run conclusions that mean "this CI step did not pass" and are worth an
# auto-fix attempt. ``cancelled`` / ``stale`` / ``skipped`` are intentionally
# excluded: they're rarely a code problem the agent can fix.
FAILING_CONCLUSIONS: frozenset[str] = frozenset(["failure", "timed_out", "action_required"])

# Check runs Open SWE itself produces; never treat them as fixable CI.
_OPEN_SWE_CHECK_NAMES: frozenset[str] = frozenset([REVIEW_CHECK_RUN_NAME, "Open SWE Auto-fix"])


class FailingCheck(dict):
    """A failing check run: ``name``, ``conclusion``, ``details_url``."""


async def list_check_runs(
    *, owner: str, repo: str, ref: str, token: str
) -> list[dict[str, Any]] | None:
    """Return the complete latest check-run set for ``ref``."""
    url = github_url(f"/repos/{owner}/{repo}/commits/{ref}/check-runs")
    try:
        async with github_client(token=token) as client:
            collected = await github_paginate(
                client, url, items_key="check_runs", params={"filter": "latest"}
            )
    except httpx.HTTPError:
        logger.warning(
            "Failed to list check runs for %s/%s@%s (Checks: Read missing?)", owner, repo, ref
        )
        return None
    return [
        run
        for run in collected
        if isinstance(run, dict) and run.get("name") not in _OPEN_SWE_CHECK_NAMES
    ]


async def list_commit_statuses(
    *, owner: str, repo: str, ref: str, token: str
) -> list[dict[str, Any]] | None:
    """Return the complete legacy commit-status set for ``ref``."""
    url = github_url(f"/repos/{owner}/{repo}/commits/{ref}/status")
    try:
        async with github_client(token=token) as client:
            collected = await github_paginate(client, url, items_key="statuses")
    except httpx.HTTPError:
        logger.warning("Failed to read combined status for %s/%s@%s", owner, repo, ref)
        return None
    latest: list[dict[str, Any]] = []
    seen_contexts: set[str] = set()
    for status in collected:
        if not isinstance(status, dict):
            continue
        context = status.get("context")
        key = context if isinstance(context, str) else ""
        if key in seen_contexts:
            continue
        seen_contexts.add(key)
        latest.append(status)
    return latest


async def list_failing_check_runs(
    *, owner: str, repo: str, ref: str, token: str
) -> list[dict[str, Any]] | None:
    """Return failing check runs on ``ref`` (commit SHA or branch)."""
    runs = await list_check_runs(owner=owner, repo=repo, ref=ref, token=token)
    if runs is None:
        return None
    return [
        {
            "name": run.get("name") or "",
            "conclusion": run.get("conclusion"),
            "details_url": run.get("details_url") or run.get("html_url") or "",
        }
        for run in runs
        if run.get("status") == "completed" and run.get("conclusion") in FAILING_CONCLUSIONS
    ]


async def list_failing_statuses(
    *, owner: str, repo: str, ref: str, token: str
) -> list[dict[str, Any]] | None:
    """Return failing legacy commit statuses on ``ref`` (the ``status`` API)."""
    statuses = await list_commit_statuses(owner=owner, repo=repo, ref=ref, token=token)
    if statuses is None:
        return None
    return [
        {
            "name": status.get("context") or "",
            "conclusion": status.get("state"),
            "details_url": status.get("target_url") or "",
        }
        for status in statuses
        if status.get("state") in {"failure", "error"}
    ]


def _failing_names(checks: list[dict[str, Any]] | None) -> set[str]:
    return {c.get("name", "") for c in (checks or []) if c.get("name")}


async def names_failing_on_base(*, owner: str, repo: str, base_sha: str, token: str) -> set[str]:
    """Return the set of check/status names already failing on ``base_sha``.

    Used to skip auto-fix for failures inherited from the base branch (the
    failure isn't introduced by the PR), matching Cursor's skip rule.
    """
    if not base_sha:
        return set()
    checks = await list_failing_check_runs(owner=owner, repo=repo, ref=base_sha, token=token)
    statuses = await list_failing_statuses(owner=owner, repo=repo, ref=base_sha, token=token)
    return _failing_names(checks) | _failing_names(statuses)


async def fetch_open_pr_for_branch(
    *, owner: str, repo: str, branch: str, token: str
) -> dict[str, Any] | None:
    """Return the first open PR whose head is ``branch`` in ``owner/repo``."""
    url = github_url(f"/repos/{owner}/{repo}/pulls")
    params = {"head": f"{owner}:{branch}", "state": "open", "per_page": "1"}
    try:
        async with github_client(token=token) as client:
            response = await github_request(client, "GET", url, params=params)
            response.raise_for_status()
    except httpx.HTTPError:
        logger.warning("Failed to find open PR for %s/%s head=%s", owner, repo, branch)
        return None
    data = response.json()
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return None


async def fetch_pr(*, owner: str, repo: str, pr_number: int, token: str) -> dict[str, Any] | None:
    """Fetch full PR metadata (includes ``mergeable_state``)."""
    url = github_url(f"/repos/{owner}/{repo}/pulls/{pr_number}")
    try:
        async with github_client(token=token) as client:
            response = await github_request(client, "GET", url)
            response.raise_for_status()
    except httpx.HTTPError:
        logger.warning("Failed to fetch PR %s/%s#%s", owner, repo, pr_number)
        return None
    data = response.json()
    return data if isinstance(data, dict) else None


async def head_commit_author_login(*, owner: str, repo: str, sha: str, token: str) -> str | None:
    """Return the GitHub login that authored commit ``sha`` (or ``None``)."""
    url = github_url(f"/repos/{owner}/{repo}/commits/{sha}")
    try:
        async with github_client(token=token) as client:
            response = await github_request(client, "GET", url)
            response.raise_for_status()
    except httpx.HTTPError:
        logger.debug("Failed to fetch commit %s/%s@%s for author check", owner, repo, sha)
        return None
    data = response.json()
    author = data.get("author") if isinstance(data, dict) else None
    login = author.get("login") if isinstance(author, dict) else None
    return login if isinstance(login, str) and login else None


async def has_repo_write_permission(*, owner: str, repo: str, username: str, token: str) -> bool:
    """Return whether ``username`` has write/maintain/admin on ``owner/repo``.

    Used to gate the no-mention auto-fix-on-review path so a triage/read-only
    reviewer can't drive code changes. Fails closed on any error.
    """
    if not username:
        return False
    url = github_url(f"/repos/{owner}/{repo}/collaborators/{username}/permission")
    try:
        async with github_client(token=token) as client:
            response = await github_request(client, "GET", url)
            response.raise_for_status()
    except httpx.HTTPError:
        logger.info("Could not verify %s's permission on %s/%s; denying", username, owner, repo)
        return False
    data = response.json()
    permission = data.get("permission") if isinstance(data, dict) else None
    return permission in {"admin", "maintain", "write"}


def branch_from_check_payload(payload: dict[str, Any], event_type: str) -> str:
    """Extract the head branch name from a CI webhook payload."""
    if event_type == "check_run":
        suite = (payload.get("check_run") or {}).get("check_suite") or {}
        return suite.get("head_branch") or ""
    if event_type == "check_suite":
        return (payload.get("check_suite") or {}).get("head_branch") or ""
    if event_type == "workflow_run":
        return (payload.get("workflow_run") or {}).get("head_branch") or ""
    if event_type == "status":
        branches = payload.get("branches")
        if isinstance(branches, list) and branches and isinstance(branches[0], dict):
            return branches[0].get("name") or ""
    return ""


def head_sha_from_check_payload(payload: dict[str, Any], event_type: str) -> str:
    """Extract the head commit SHA from a CI webhook payload."""
    if event_type == "check_run":
        return (payload.get("check_run") or {}).get("head_sha") or ""
    if event_type == "check_suite":
        return (payload.get("check_suite") or {}).get("head_sha") or ""
    if event_type == "workflow_run":
        return (payload.get("workflow_run") or {}).get("head_sha") or ""
    if event_type == "status":
        return payload.get("sha") or ""
    return ""


def is_failing_ci_payload(payload: dict[str, Any], event_type: str) -> bool:
    """Return whether a CI webhook payload represents a completed failure."""
    if event_type in {"check_run", "check_suite", "workflow_run"}:
        node = payload.get(event_type) or {}
        if node.get("status") != "completed":
            return False
        return node.get("conclusion") in FAILING_CONCLUSIONS
    if event_type == "status":
        return payload.get("state") in {"failure", "error"}
    return False
