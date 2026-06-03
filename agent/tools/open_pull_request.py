"""Open a GitHub pull request attributed to the triggering user."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from langgraph.config import get_config

from ..utils.github_app import get_github_app_installation_token

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
