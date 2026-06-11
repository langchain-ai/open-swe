"""Open a GitHub pull request attributed to the triggering user."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from langgraph.config import get_config
from langgraph_sdk import get_client

from ..dashboard.agent_usage import record_agent_pr_usage
from ..utils.github_app import get_github_app_installation_token
from ..utils.github_comments import derive_pr_state

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
_USER_TOKEN_SOURCES = ("slack", "dashboard")


async def _resolve_pr_author_token() -> tuple[str | None, str]:
    """Return ``(token, kind)`` for opening the PR.

    Prefers the triggering user's OAuth token (so the PR is created *as them*)
    for Slack/dashboard runs with a mapped GitHub login, resolving it by login
    from the dashboard OAuth store. Falls back to the GitHub App installation
    token (creator = open-swe[bot]) for GitHub-triggered runs, unmapped users,
    or bot-token-only deployments — preserving today's behavior.

    The token is resolved by login rather than read from the shared thread
    metadata: Slack thread ids are shared across a conversation, so a cached
    token could belong to a prior triggering user.
    """
    configurable = get_config().get("configurable", {})
    source = configurable.get("source")
    github_login = configurable.get("github_login")

    if source in _USER_TOKEN_SOURCES and isinstance(github_login, str) and github_login.strip():
        from ..dashboard.profiles import get_valid_access_token

        user_token = await get_valid_access_token(github_login.strip())
        if user_token:
            return user_token, "user"
        logger.info("No valid user token for %s; opening PR as open-swe[bot]", github_login.strip())

    return await get_github_app_installation_token(), "bot"


def _auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _find_existing_pr(
    client: httpx.AsyncClient, token: str, owner: str, repo: str, head: str
) -> dict[str, Any] | None:
    resp = await client.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
        headers=_auth_headers(token),
        params={"head": f"{owner}:{head}", "state": "open"},
    )
    if resp.status_code != 200:
        return None
    items = resp.json()
    return items[0] if isinstance(items, list) and items else None


async def _fetch_pr_details(
    client: httpx.AsyncClient, token: str, owner: str, repo: str, pr_number: int
) -> dict[str, Any]:
    resp = await client.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}",
        headers=_auth_headers(token),
    )
    if resp.status_code != 200:
        logger.debug(
            "GitHub returned %s fetching PR stats for %s/%s#%s: %s",
            resp.status_code,
            owner,
            repo,
            pr_number,
            resp.text,
        )
        return {}
    data = resp.json()
    return data if isinstance(data, dict) else {}


async def _record_pr_telemetry(
    *,
    client: httpx.AsyncClient,
    token: str,
    owner: str,
    repo: str,
    head: str,
    base: str,
    pr: dict[str, Any],
) -> None:
    pr_number = pr.get("number")
    if not isinstance(pr_number, int):
        return
    try:
        details = await _fetch_pr_details(client, token, owner, repo, pr_number)
        config = get_config()
        configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
        thread_id = configurable.get("thread_id")
        github_login = configurable.get("github_login")
        user_email = configurable.get("user_email")
        if not isinstance(github_login, str) or not github_login.strip():
            from ..dashboard.user_mappings import login_for_email

            github_login = (
                await login_for_email(user_email if isinstance(user_email, str) else None) or ""
            )
        pr_url = details.get("html_url") or pr.get("html_url")
        merged = bool(details.get("merged"))
        is_draft = bool(details.get("draft", pr.get("draft")))
        state = details.get("state") if isinstance(details.get("state"), str) else "open"
        additions = details.get("additions") if isinstance(details.get("additions"), int) else 0
        deletions = details.get("deletions") if isinstance(details.get("deletions"), int) else 0
        changed_files = (
            details.get("changed_files") if isinstance(details.get("changed_files"), int) else 0
        )
        await record_agent_pr_usage(
            thread_id=thread_id if isinstance(thread_id, str) else None,
            github_login=github_login,
            user_email=user_email if isinstance(user_email, str) else None,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            pr_url=pr_url if isinstance(pr_url, str) else None,
            head=head,
            base=base,
            additions=additions,
            deletions=deletions,
            changed_files=changed_files,
            state=state,
            merged=merged,
        )
        if isinstance(thread_id, str) and thread_id:
            await get_client().threads.update(
                thread_id=thread_id,
                metadata={
                    "agent_kind": "agent",
                    "pr_url": pr_url if isinstance(pr_url, str) else "",
                    "pr_number": pr_number,
                    "pr_state": derive_pr_state(state=state, merged=merged, draft=is_draft),
                    "pr_title": details.get("title") or pr.get("title"),
                    "branch_name": head,
                    "base_branch": base,
                    "diff_stats": {
                        "files": changed_files,
                        "additions": additions,
                        "deletions": deletions,
                    },
                },
            )
    except Exception:
        logger.debug(
            "Failed to record PR usage for %s/%s#%s", owner, repo, pr_number, exc_info=True
        )


async def _open_pull_request(
    *,
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str,
    draft: bool,
) -> dict[str, Any]:
    token, kind = await _resolve_pr_author_token()
    if not token:
        return {
            "success": False,
            "error": "No GitHub token available to open the pull request.",
        }

    payload = {"title": title, "head": head, "base": base, "body": body, "draft": draft}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
            headers=_auth_headers(token),
            json=payload,
        )
        if resp.status_code == 201:
            pr = resp.json()
            if isinstance(pr, dict):
                await _record_pr_telemetry(
                    client=client,
                    token=token,
                    owner=owner,
                    repo=repo,
                    head=head,
                    base=base,
                    pr=pr,
                )
            return {
                "success": True,
                "created": True,
                "url": pr.get("html_url"),
                "number": pr.get("number"),
                "author": (pr.get("user") or {}).get("login"),
                "token_kind": kind,
            }

        # A PR for this head branch may already exist — return it so the agent
        # switches to `gh pr edit` for updates instead of erroring out.
        if resp.status_code == 422:  # noqa: PLR2004
            existing = await _find_existing_pr(client, token, owner, repo, head)
            if existing is not None:
                await _record_pr_telemetry(
                    client=client,
                    token=token,
                    owner=owner,
                    repo=repo,
                    head=head,
                    base=base,
                    pr=existing,
                )
                return {
                    "success": True,
                    "created": False,
                    "url": existing.get("html_url"),
                    "number": existing.get("number"),
                    "author": (existing.get("user") or {}).get("login"),
                    "token_kind": kind,
                }

        return {
            "success": False,
            "error": f"GitHub returned {resp.status_code}: {resp.text}",
        }


def open_pull_request(
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
    commenting, reading status — keep using `GH_TOKEN=dummy gh`. If a PR already
    exists for the branch, this returns that PR's URL without creating a
    duplicate; switch to `gh pr edit` for updates.

    Args:
        owner: Repository owner/org (e.g. "langchain-ai").
        repo: Repository name (e.g. "open-swe").
        head: The branch with your changes (already pushed to origin).
        base: The branch you want to merge into (e.g. "main").
        title: PR title.
        body: PR description (Markdown).
        draft: Open as a draft PR. Defaults to True.

    Returns:
        On success: {"success": True, "created": bool, "url": str, "number": int,
        "author": str}. ``created`` is False when an open PR already existed.
        On failure: {"success": False, "error": str}.
    """
    return asyncio.run(
        _open_pull_request(
            owner=owner,
            repo=repo,
            head=head,
            base=base,
            title=title,
            body=body,
            draft=draft,
        )
    )
