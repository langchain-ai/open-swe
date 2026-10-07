"""GitHub CI read helpers for auto-fixing failing checks on agent PRs.

These read third-party CI results (GitHub Actions check runs, the legacy
combined commit status) so the auto-fix flow can detect failures and dedupe
per commit.

All calls are best-effort: they require the GitHub App's ``Checks: Read``
permission, and a missing permission or transient error must never break
webhook handling.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Self

import httpx2
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from openswe.github.checks import REVIEW_CHECK_RUN_NAME
from openswe.github.http import RepoClient, or_none

logger = logging.getLogger(__name__)

# Check-run conclusions that mean "this CI step did not pass" and are worth an
# auto-fix attempt. ``cancelled`` / ``stale`` / ``skipped`` are intentionally
# excluded: they're rarely a code problem the agent can fix.
FAILING_CONCLUSIONS: frozenset[str] = frozenset(["failure", "timed_out", "action_required"])

# Check runs Open SWE itself produces; never treat them as fixable CI.
_OPEN_SWE_CHECK_NAMES: frozenset[str] = frozenset([REVIEW_CHECK_RUN_NAME, "Open SWE Auto-fix"])


@dataclass(frozen=True, slots=True)
class CommitChecks:
    """Third-party CI on one commit: its latest check runs and its latest commit statuses."""

    runs: list[dict[str, Any]]
    statuses: list[dict[str, Any]]

    @classmethod
    async def read(cls, repo: RepoClient, sha: str) -> Self | None:
        """``None`` when GitHub could not answer for either (Checks: Read missing?)."""
        runs, statuses = await asyncio.gather(
            or_none(repo.check_runs(sha)), or_none(repo.commit_statuses(sha))
        )
        if runs is None or statuses is None:
            logger.warning(
                "Commit checks unavailable", extra={"repo_full_name": repo.full_name, "sha": sha}
            )
            return None
        return cls(
            runs=[run for run in runs if run.get("name") not in _OPEN_SWE_CHECK_NAMES],
            statuses=statuses,
        )

    def unreported(self, required: set[RequiredCheck]) -> list[str]:
        return unreported_required_checks(required, self.runs, self.statuses)


@dataclass(frozen=True, slots=True)
class RequiredCheck:
    """A check a branch requires; ``app_id`` pins the GitHub App that must report it."""

    name: str
    app_id: int | None = None

    @classmethod
    async def for_branch(cls, repo: RepoClient, branch: str) -> set[Self] | None:
        """Checks ``branch`` requires, from branch protection and rulesets; read access suffices."""
        try:
            protection = _Branch.model_validate(await repo.get(f"branches/{branch}")).protection
            rules = _BRANCH_RULES.validate_python(await repo.pages(f"rules/branches/{branch}"))
        except httpx2.HTTPError, ValueError, ValidationError:
            logger.warning(
                "Failed to read required checks",
                extra={"repo_full_name": repo.full_name, "branch": branch},
                exc_info=True,
            )
            return None
        required: set[Self] = set()
        classic = protection.required_status_checks if protection else None
        if classic is not None:
            required.update(cls(context) for context in classic.contexts)
            required.update(cls(check.context, _any_app(check.app_id)) for check in classic.checks)
        for rule in rules:
            if rule.type == "required_status_checks" and rule.parameters is not None:
                required.update(
                    cls(check.context, _any_app(check.integration_id))
                    for check in rule.parameters.required_status_checks
                )
        return {check for check in required if check.name not in _OPEN_SWE_CHECK_NAMES}

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


def unreported_required_checks(
    required: set[RequiredCheck], check_runs: list[dict[str, Any]], statuses: list[dict[str, Any]]
) -> list[str]:
    """Required check names with no matching check run or commit status on the head yet."""
    return sorted({check.name for check in required if not check.reported_by(check_runs, statuses)})


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
