"""GitHub CI read helpers for auto-fixing failing checks on agent PRs.

These read third-party CI results (GitHub Actions check runs, the legacy
combined commit status) so the auto-fix flow can detect failures, dedupe per
commit, and decide whether a failure is pre-existing on the base branch.

All calls are best-effort: they require the GitHub App's ``Checks: Read``
permission, and a missing permission or transient error must never break
webhook handling.
"""

import logging
from dataclasses import dataclass
from typing import Any

import httpx2
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from agent.github.checks import REVIEW_CHECK_RUN_NAME
from agent.github.http import GITHUB_API_BASE, github_client, github_request

logger = logging.getLogger(__name__)

_GITHUB_API_BASE = GITHUB_API_BASE

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
    url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{ref}/check-runs"
    collected: list[dict[str, Any]] = []
    page = 1
    try:
        async with github_client(token=token) as client:
            while True:
                response = await github_request(
                    client,
                    "GET",
                    url,
                    params={"per_page": "100", "page": str(page), "filter": "latest"},
                )
                response.raise_for_status()
                data = response.json()
                runs = data.get("check_runs") if isinstance(data, dict) else None
                if not isinstance(runs, list):
                    break
                page_runs = [run for run in runs if isinstance(run, dict)]
                collected.extend(page_runs)
                if len(runs) < 100:
                    break
                page += 1
    except httpx2.HTTPError:
        logger.warning(
            "Failed to list check runs for %s/%s@%s (Checks: Read missing?)", owner, repo, ref
        )
        return None
    return [run for run in collected if run.get("name") not in _OPEN_SWE_CHECK_NAMES]


async def list_commit_statuses(
    *, owner: str, repo: str, ref: str, token: str
) -> list[dict[str, Any]] | None:
    """Return the complete legacy commit-status set for ``ref``."""
    url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{ref}/status"
    collected: list[dict[str, Any]] = []
    page = 1
    try:
        async with github_client(token=token) as client:
            while True:
                response = await github_request(
                    client,
                    "GET",
                    url,
                    params={"per_page": "100", "page": str(page)},
                )
                response.raise_for_status()
                data = response.json()
                statuses = data.get("statuses") if isinstance(data, dict) else None
                if not isinstance(statuses, list):
                    break
                page_statuses = [status for status in statuses if isinstance(status, dict)]
                collected.extend(page_statuses)
                if len(statuses) < 100:
                    break
                page += 1
    except httpx2.HTTPError:
        logger.warning("Failed to read combined status for %s/%s@%s", owner, repo, ref)
        return None
    latest: list[dict[str, Any]] = []
    seen_contexts: set[str] = set()
    for status in collected:
        context = status.get("context")
        key = context if isinstance(context, str) else ""
        if key in seen_contexts:
            continue
        seen_contexts.add(key)
        latest.append(status)
    return latest


@dataclass(frozen=True, slots=True)
class RequiredCheck:
    """A check a branch requires; ``app_id`` pins the GitHub App that must report it."""

    name: str
    app_id: int | None = None

    def reported_by(self, check_runs: list[dict[str, Any]], statuses: list[dict[str, Any]]) -> bool:
        for run in check_runs:
            if run.get("name") != self.name:
                continue
            app = run.get("app")
            if self.app_id is None or (isinstance(app, dict) and app.get("id") == self.app_id):
                return True
        return self.app_id is None and any(
            status.get("context") == self.name for status in statuses
        )


class _ProtectionCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")

    context: str
    app_id: int | None = None


class _RulesetCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")

    context: str
    integration_id: int | None = None


class _RequiredStatusChecks(BaseModel):
    model_config = ConfigDict(extra="ignore")

    contexts: list[str] = Field(default_factory=list)
    checks: list[_ProtectionCheck] = Field(default_factory=list)


class _BranchProtection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    required_status_checks: _RequiredStatusChecks | None = None


class _Branch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    protection: _BranchProtection | None = None


class _RuleParameters(BaseModel):
    model_config = ConfigDict(extra="ignore")

    required_status_checks: list[_RulesetCheck] = Field(default_factory=list)


class _BranchRule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    parameters: _RuleParameters | None = None


_BRANCH_RULES = TypeAdapter(list[_BranchRule])


def _any_app(app_id: int | None) -> int | None:
    """GitHub stores "any app" as a missing id or -1."""
    return app_id if app_id is not None and app_id > 0 else None


async def fetch_required_checks(
    *, owner: str, repo: str, branch: str, token: str
) -> set[RequiredCheck] | None:
    """Checks ``branch`` requires, from branch protection and rulesets; read access suffices."""
    async with github_client(token=token) as client:
        return await read_required_checks(client, owner=owner, repo=repo, branch=branch)


async def read_required_checks(
    client: httpx2.AsyncClient, *, owner: str, repo: str, branch: str
) -> set[RequiredCheck] | None:
    base = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}"
    rules: list[_BranchRule] = []
    try:
        branch_response = await github_request(client, "GET", f"{base}/branches/{branch}")
        branch_response.raise_for_status()
        protection = _Branch.model_validate(branch_response.json()).protection
        page = 1
        while True:
            rules_response = await github_request(
                client,
                "GET",
                f"{base}/rules/branches/{branch}",
                params={"per_page": "100", "page": str(page)},
            )
            rules_response.raise_for_status()
            page_rules = _BRANCH_RULES.validate_python(rules_response.json())
            rules.extend(page_rules)
            if len(page_rules) < 100:
                break
            page += 1
    except httpx2.HTTPError, ValueError, ValidationError:
        logger.warning(
            "Failed to read required checks",
            extra={"repo_full_name": f"{owner}/{repo}", "branch": branch},
            exc_info=True,
        )
        return None
    required: set[RequiredCheck] = set()
    classic = protection.required_status_checks if protection else None
    if classic is not None:
        required.update(RequiredCheck(context) for context in classic.contexts)
        required.update(
            RequiredCheck(check.context, _any_app(check.app_id)) for check in classic.checks
        )
    for rule in rules:
        if rule.type == "required_status_checks" and rule.parameters is not None:
            required.update(
                RequiredCheck(check.context, _any_app(check.integration_id))
                for check in rule.parameters.required_status_checks
            )
    return {check for check in required if check.name not in _OPEN_SWE_CHECK_NAMES}


def unreported_required_checks(
    required: set[RequiredCheck], check_runs: list[dict[str, Any]], statuses: list[dict[str, Any]]
) -> list[str]:
    """Required check names with no matching check run or commit status on the head yet."""
    return sorted({check.name for check in required if not check.reported_by(check_runs, statuses)})


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
    url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/pulls"
    params = {"head": f"{owner}:{branch}", "state": "open", "per_page": "1"}
    try:
        async with github_client(token=token) as client:
            response = await github_request(client, "GET", url, params=params)
            response.raise_for_status()
    except httpx2.HTTPError:
        logger.warning("Failed to find open PR for %s/%s head=%s", owner, repo, branch)
        return None
    data = response.json()
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return None


async def fetch_pr(*, owner: str, repo: str, pr_number: int, token: str) -> dict[str, Any] | None:
    """Fetch full PR metadata (includes ``mergeable_state``)."""
    url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}"
    try:
        async with github_client(token=token) as client:
            response = await github_request(client, "GET", url)
            response.raise_for_status()
    except httpx2.HTTPError:
        logger.warning("Failed to fetch PR %s/%s#%s", owner, repo, pr_number)
        return None
    data = response.json()
    return data if isinstance(data, dict) else None


async def head_commit_author_login(*, owner: str, repo: str, sha: str, token: str) -> str | None:
    """Return the GitHub login that authored commit ``sha`` (or ``None``)."""
    url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{sha}"
    try:
        async with github_client(token=token) as client:
            response = await github_request(client, "GET", url)
            response.raise_for_status()
    except httpx2.HTTPError:
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
    url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/collaborators/{username}/permission"
    try:
        async with github_client(token=token) as client:
            response = await github_request(client, "GET", url)
            response.raise_for_status()
    except httpx2.HTTPError:
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


def is_completed_ci_payload(payload: dict[str, Any], event_type: str) -> bool:
    """Return whether a CI webhook payload reports a finished check, whatever its outcome."""
    if event_type in {"check_run", "check_suite", "workflow_run"}:
        return (payload.get(event_type) or {}).get("status") == "completed"
    if event_type == "status":
        return payload.get("state") in {"success", "failure", "error"}
    return False
