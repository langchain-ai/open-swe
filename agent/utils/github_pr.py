"""Pull-request preflight and the failure taxonomy the agent reads back.

``open_pull_request`` returns structured failures rather than raising, because
the agent has to decide what to do next from the payload alone: whether the
branch was pushed, whether a PR exists, whether a human has to grant access.
Building those payloads — and the preflight that distinguishes "repo invisible"
from "branch missing" before GitHub collapses everything into a 404 — is what
this module owns.
"""

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from .github_http import github_error_message, github_request, github_url

logger = logging.getLogger(__name__)

ACCESS_FAILURE_CODE = "github_app_access_missing_or_repo_not_found"
BRANCH_FAILURE_CODE = "github_pr_branch_not_visible"
PREFLIGHT_FAILURE_CODE = "github_pr_preflight_failed"

_ACCESS_LIKELY_CAUSE = (
    "the Open SWE GitHub App or PR author token is not installed on, granted access "
    "to, or able to see this repository or one of the PR branches"
)
_ACCESS_SUGGESTED_ACTION = (
    "install or grant the Open SWE GitHub App and the triggering user's GitHub "
    "authorization access to this repository, verify the base/head branches exist, "
    "then ask Open SWE to retry opening the PR"
)


def head_branch_for_repo(owner: str, head: str) -> str | None:
    """The head branch name when it lives in ``owner``'s repo, else ``None``.

    A cross-fork ``head`` (``other-owner:branch``) is not a branch of the base
    repo, so it can't be preflighted there.
    """
    if ":" not in head:
        return head
    head_owner, branch = head.split(":", 1)
    if head_owner == owner and branch:
        return branch
    return None


@dataclass(frozen=True)
class PullRequestAttempt:
    """One attempt to open a PR, and the failures it can report."""

    owner: str
    repo: str
    head: str
    base: str
    token_kind: str
    configurable: dict[str, Any]

    def failure(
        self,
        *,
        code: str,
        http_status: int | None,
        reason: str,
        likely_cause: str,
        suggested_action: str,
        branch_pushed: bool | None,
        failed_step: str,
        repo_visible: bool | None = None,
        base_branch_visible: bool | None = None,
        head_branch_visible: bool | None = None,
    ) -> dict[str, Any]:
        error = (
            "Failed to open an attributed PR with open_pull_request. "
            f"Reason: {reason}. Likely cause: {likely_cause}. "
            f"Branch pushed: {self.owner}/{self.repo}:{self.head} "
            f"({'unknown' if branch_pushed is None else 'yes' if branch_pushed else 'no'}). "
            "PR created: no. "
            f"Action: {suggested_action}"
        )
        payload: dict[str, Any] = {
            "success": False,
            "error": error,
            "code": code,
            "recoverable_by_agent": False,
            "owner": self.owner,
            "repo": self.repo,
            "head": self.head,
            "base": self.base,
            "token_kind": self.token_kind,
            "http_status": http_status,
            "branch_pushed": branch_pushed,
            "pr_created": False,
            "failed_step": failed_step,
            "likely_cause": likely_cause,
            "suggested_action": suggested_action,
        }
        if repo_visible is not None:
            payload["repo_visible"] = repo_visible
        if base_branch_visible is not None:
            payload["base_branch_visible"] = base_branch_visible
        if head_branch_visible is not None:
            payload["head_branch_visible"] = head_branch_visible
        self._log(payload)
        return payload

    def access_failure(
        self,
        *,
        http_status: int | None,
        reason: str,
        branch_pushed: bool | None,
        failed_step: str,
        repo_visible: bool | None = None,
        base_branch_visible: bool | None = None,
        head_branch_visible: bool | None = None,
    ) -> dict[str, Any]:
        return self.failure(
            code=ACCESS_FAILURE_CODE,
            http_status=http_status,
            reason=reason,
            likely_cause=_ACCESS_LIKELY_CAUSE,
            suggested_action=_ACCESS_SUGGESTED_ACTION,
            branch_pushed=branch_pushed,
            failed_step=failed_step,
            repo_visible=repo_visible,
            base_branch_visible=base_branch_visible,
            head_branch_visible=head_branch_visible,
        )

    def branch_failure(self, *, http_status: int, branch: str, branch_role: str) -> dict[str, Any]:
        return self.failure(
            code=BRANCH_FAILURE_CODE,
            http_status=http_status,
            reason=(f"GitHub could not see the {branch_role} branch `{branch}` before PR creation"),
            likely_cause=(
                f"the {branch_role} branch does not exist on `{self.owner}/{self.repo}` or is "
                "not visible to the PR author token"
            ),
            suggested_action=(
                f"push or restore the {branch_role} branch `{branch}`, ensure the Open SWE "
                "GitHub App/token can see it, then ask Open SWE to retry opening the PR"
            ),
            branch_pushed=False if branch_role == "head" else None,
            failed_step=f"preflight_{branch_role}_branch",
            repo_visible=True,
            base_branch_visible=False if branch_role == "base" else True,
            head_branch_visible=False if branch_role == "head" else None,
        )

    def preflight_failure(
        self,
        *,
        response: httpx.Response,
        subject: str,
        cause: str,
        failed_step: str,
        **visibility: bool | None,
    ) -> dict[str, Any]:
        return self.failure(
            code=PREFLIGHT_FAILURE_CODE,
            http_status=response.status_code,
            reason=(
                f"GitHub returned {response.status_code} while checking {subject} access: "
                f"{github_error_message(response)}"
            ),
            likely_cause=f"GitHub {cause} access preflight failed before PR creation",
            suggested_action=f"check GitHub availability and {cause} access, then retry",
            branch_pushed=None,
            failed_step=failed_step,
            **visibility,
        )

    def _log(self, payload: dict[str, Any]) -> None:
        fields = {
            key: payload.get(key)
            for key in (
                "code",
                "owner",
                "repo",
                "head",
                "base",
                "http_status",
                "token_kind",
                "branch_pushed",
                "pr_created",
                "failed_step",
            )
        }
        fields["thread_id"] = self.configurable.get("thread_id")
        fields["source"] = self.configurable.get("source")
        logger.warning(
            "open_pull_request_failed code=%s owner=%s repo=%s head=%s base=%s "
            "http_status=%s token_kind=%s branch_pushed=%s thread_id=%s source=%s",
            fields["code"],
            fields["owner"],
            fields["repo"],
            fields["head"],
            fields["base"],
            fields["http_status"],
            fields["token_kind"],
            fields["branch_pushed"],
            fields["thread_id"],
            fields["source"],
            extra={"open_pull_request_failure": fields},
        )


async def preflight_pr_access(
    client: httpx.AsyncClient, attempt: PullRequestAttempt
) -> dict[str, Any] | None:
    """Check repo and branch visibility, returning a failure payload or ``None``.

    GitHub answers "you can't see this repo", "that branch doesn't exist" and
    "you lack permission" all with a 404 on the create call, so the distinction
    is only available by asking about each thing separately first.
    """
    owner, repo = attempt.owner, attempt.repo

    repo_resp = await github_request(client, "GET", github_url(f"/repos/{owner}/{repo}"))
    if repo_resp.status_code in {403, 404}:
        return attempt.access_failure(
            http_status=repo_resp.status_code,
            reason=f"GitHub returned {repo_resp.status_code} while checking repository access",
            branch_pushed=None,
            failed_step="preflight_repo",
            repo_visible=False,
        )
    if repo_resp.status_code != 200:
        return attempt.preflight_failure(
            response=repo_resp,
            subject="repository",
            cause="repository",
            failed_step="preflight_repo",
            repo_visible=None,
        )

    base_failure = await _preflight_branch(client, attempt, attempt.base, "base")
    if base_failure is not None:
        return base_failure

    head_branch = head_branch_for_repo(owner, attempt.head)
    if head_branch is None:
        return None
    return await _preflight_branch(client, attempt, head_branch, "head")


async def _preflight_branch(
    client: httpx.AsyncClient,
    attempt: PullRequestAttempt,
    branch: str,
    branch_role: str,
) -> dict[str, Any] | None:
    is_head = branch_role == "head"
    failed_step = f"preflight_{branch_role}_branch"
    response = await github_request(
        client,
        "GET",
        github_url(f"/repos/{attempt.owner}/{attempt.repo}/branches/{quote(branch, safe='')}"),
    )
    if response.status_code == 404:
        return attempt.branch_failure(
            http_status=response.status_code, branch=branch, branch_role=branch_role
        )
    if response.status_code in {401, 403}:
        return attempt.access_failure(
            http_status=response.status_code,
            reason=(
                f"GitHub returned {response.status_code} while checking {branch_role} branch access"
            ),
            branch_pushed=False if is_head else None,
            failed_step=failed_step,
            repo_visible=True,
            base_branch_visible=True if is_head else False,
            head_branch_visible=False if is_head else None,
        )
    if response.status_code != 200:
        return attempt.preflight_failure(
            response=response,
            subject=f"{branch_role} branch",
            cause="branch",
            failed_step=failed_step,
            repo_visible=True,
            base_branch_visible=True if is_head else None,
        )
    return None


async def is_private_repo(client: httpx.AsyncClient, owner: str, repo: str) -> bool:
    """True only when GitHub confirms the repo is private."""
    response = await github_request(client, "GET", github_url(f"/repos/{owner}/{repo}"))
    if response.status_code != 200:  # noqa: PLR2004
        return False
    data = response.json()
    return bool(data.get("private")) if isinstance(data, dict) else False
