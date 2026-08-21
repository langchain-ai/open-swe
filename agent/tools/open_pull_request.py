"""Open a GitHub pull request attributed to the triggering user."""

import logging
from typing import Any

import httpx

from ..settings.agent_usage import record_pr_opened
from ..utils.github_app import get_github_app_installation_token
from ..utils.github_http import github_client, github_error_message, github_request, github_url
from ..utils.github_pr import PullRequestAttempt, head_branch_for_repo, preflight_pr_access
from ..utils.run_references import append_references, run_configurable

logger = logging.getLogger(__name__)

_USER_TOKEN_SOURCES = ("slack", "linear", "dashboard")


async def _resolve_pr_author_token(configurable: dict[str, Any]) -> tuple[str | None, str]:
    """Return ``(token, kind)`` for opening the PR.

    Prefers the triggering user's OAuth token (so the PR is created *as them*)
    for Slack/Linear/dashboard runs with a mapped GitHub login, resolving it by
    login from the dashboard OAuth store. Falls back to the GitHub App
    installation token (creator = open-swe[bot]) for GitHub-triggered runs,
    unmapped users, or bot-token-only deployments.

    The token is resolved by login rather than read from the shared thread
    metadata: Slack thread ids are shared across a conversation, so a cached
    token could belong to a prior triggering user.
    """
    source = configurable.get("source")
    github_login = configurable.get("github_login")

    if source in _USER_TOKEN_SOURCES and isinstance(github_login, str) and github_login.strip():
        from ..settings.github_tokens import get_valid_access_token

        user_token = await get_valid_access_token(github_login.strip())
        if user_token:
            return user_token, "user"
        logger.info("No valid user token for %s; opening PR as open-swe[bot]", github_login.strip())

    return await get_github_app_installation_token(), "bot"


def _effective_draft(configurable: dict[str, Any], draft: bool) -> bool:
    preference = configurable.get("draft_prs")
    return preference if isinstance(preference, bool) else draft


async def _find_existing_pr(
    client: httpx.AsyncClient, owner: str, repo: str, head: str
) -> dict[str, Any] | None:
    resp = await github_request(
        client,
        "GET",
        github_url(f"/repos/{owner}/{repo}/pulls"),
        params={"head": f"{owner}:{head}", "state": "open"},
    )
    if resp.status_code != 200:
        return None
    items = resp.json()
    return items[0] if isinstance(items, list) and items else None


def _success(pr: dict[str, Any], *, created: bool, token_kind: str) -> dict[str, Any]:
    return {
        "success": True,
        "created": created,
        "url": pr.get("html_url"),
        "number": pr.get("number"),
        "author": (pr.get("user") or {}).get("login"),
        "token_kind": token_kind,
    }


async def open_pull_request(
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str,
    draft: bool = True,
) -> dict[str, Any]:
    """Open a draft GitHub pull request attributed to the triggering user.

    Use this to OPEN a NEW pull request (instead of `gh pr create`) so the PR is
    created as the person who triggered the run rather than open-swe[bot]. Push
    your branch with `git push origin <branch>` BEFORE calling this.

    For everything else — updating an existing PR, marking it ready for review,
    commenting, reading status — keep using `gh`. If a PR already
    exists for the branch, this returns that PR's URL without creating a
    duplicate; switch to `gh pr edit` for updates.

    Args:
        owner: Repository owner/org (e.g. "langchain-ai").
        repo: Repository name (e.g. "open-swe").
        head: The branch with your changes (already pushed to origin).
        base: The branch you want to merge into (e.g. "main").
        title: PR title.
        body: PR description (Markdown).
        draft: Requested draft status. The authenticated user's dashboard preference
          overrides this value for newly created PRs; existing PRs are returned unchanged.

    Returns:
        On success: {"success": True, "created": bool, "url": str, "number": int,
        "author": str}. ``created`` is False when an open PR already existed.
        On failure: {"success": False, "error": str, "code": str,
        "recoverable_by_agent": False, "pr_created": False, ...}.
    """
    configurable = run_configurable()
    token, kind = await _resolve_pr_author_token(configurable)
    attempt = PullRequestAttempt(
        owner=owner,
        repo=repo,
        head=head,
        base=base,
        token_kind=kind,
        configurable=configurable,
    )
    if not token:
        return attempt.failure(
            code="no_github_token",
            http_status=None,
            reason="No GitHub token was available to open the pull request",
            likely_cause="the triggering user is not authorized and no GitHub App token is available",
            suggested_action="connect GitHub authorization or install/grant the Open SWE GitHub App, then retry",
            branch_pushed=None,
            failed_step="resolve_pr_author_token",
        )

    async with github_client(token=token) as client:
        preflight_failure = await preflight_pr_access(client, attempt)
        if preflight_failure is not None:
            return preflight_failure

        resp = await github_request(
            client,
            "POST",
            github_url(f"/repos/{owner}/{repo}/pulls"),
            json={
                "title": title,
                "head": head,
                "base": base,
                "body": await append_references(
                    client, configurable, owner=owner, repo=repo, body=body
                ),
                "draft": _effective_draft(configurable, draft),
            },
        )
        if resp.status_code == 201:
            pr = resp.json()
            if isinstance(pr, dict):
                await record_pr_opened(
                    pr,
                    configurable=configurable,
                    owner=owner,
                    repo=repo,
                    head=head,
                    base=base,
                    token=token,
                )
            return _success(pr, created=True, token_kind=kind)

        # A PR for this head branch may already exist — return it so the agent
        # switches to `gh pr edit` for updates instead of erroring out.
        if resp.status_code == 422:  # noqa: PLR2004
            existing = await _find_existing_pr(client, owner, repo, head)
            if existing is not None:
                await record_pr_opened(
                    existing,
                    configurable=configurable,
                    owner=owner,
                    repo=repo,
                    head=head,
                    base=base,
                    token=token,
                )
                return _success(existing, created=False, token_kind=kind)

        head_visible = True if head_branch_for_repo(owner, head) is not None else None
        if resp.status_code == 404:
            return attempt.access_failure(
                http_status=resp.status_code,
                reason="GitHub returned 404 while creating the pull request",
                branch_pushed=True,
                failed_step="create_pull_request",
                repo_visible=True,
                base_branch_visible=True,
                head_branch_visible=head_visible,
            )

        return attempt.failure(
            code="github_pr_create_failed",
            http_status=resp.status_code,
            reason=(
                f"GitHub returned {resp.status_code} while creating the pull request: "
                f"{github_error_message(resp)}"
            ),
            likely_cause="GitHub rejected the pull request creation request",
            suggested_action="inspect the GitHub error, correct the branch or repository state, then retry",
            branch_pushed=True,
            failed_step="create_pull_request",
            repo_visible=True,
            base_branch_visible=True,
            head_branch_visible=head_visible,
        )
