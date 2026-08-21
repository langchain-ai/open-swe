"""What the Open SWE agent did: one record per thread, one per pull request.

Write-only, and on the agent's critical path — a run records its own thread as
it starts and its own PR as it opens one. Reading these back into a leaderboard
is the dashboard's job (``agent.dashboard.usage_reports``), which is why the
aggregation lives up there and not here: it needs the reviewer's findings, and
the foundation must not reach into the review domain.
"""

import logging
from typing import Any

import httpx
from langgraph_sdk import get_client

from ..github.api import github_client, github_request, github_url
from ..github.comments import derive_pr_state
from ..github.refs import parse_github_pr_url
from ..store import get_value, now_ms, put_value

USAGE_THREAD_NAMESPACE: list[str] = ["agent_usage", "threads"]
USAGE_PR_NAMESPACE: list[str] = ["agent_usage", "prs"]

AGENT_SOURCES = frozenset({"dashboard", "github", "slack", "linear"})

logger = logging.getLogger(__name__)


def _client():
    return get_client()


async def record_agent_thread_usage(
    *,
    thread_id: str,
    github_login: str | None,
    user_email: str | None,
    model_id: str,
    effort: str | None,
    source: str | None,
) -> None:
    """Record one Open SWE Agent thread for leaderboard aggregation."""
    if not thread_id:
        return
    source_value = source if isinstance(source, str) and source in AGENT_SOURCES else "dashboard"
    stamp = now_ms()
    existing = await get_value(USAGE_THREAD_NAMESPACE, thread_id)
    value = {
        **(existing or {}),
        "thread_id": thread_id,
        "github_login": github_login.strip() if isinstance(github_login, str) else "",
        "user_email": user_email.strip().lower() if isinstance(user_email, str) else "",
        "model_id": model_id,
        "effort": effort or "",
        "source": source_value,
        "agent_kind": "agent",
        "updated_at_ms": stamp,
    }
    if not existing:
        value["created_at_ms"] = stamp
    elif not value.get("created_at_ms"):
        value["created_at_ms"] = existing.get("created_at_ms") or stamp
    await put_value(USAGE_THREAD_NAMESPACE, thread_id, value)


async def record_agent_pr_usage(
    *,
    thread_id: str | None,
    github_login: str | None,
    user_email: str | None,
    owner: str,
    repo: str,
    pr_number: int,
    pr_url: str | None,
    head: str,
    base: str,
    additions: int = 0,
    deletions: int = 0,
    changed_files: int = 0,
    state: str | None = None,
    merged: bool = False,
) -> None:
    """Record one Open SWE Agent pull request for leaderboard aggregation."""
    if not owner or not repo or not pr_number:
        return
    key = f"{owner}/{repo}#{pr_number}"
    stamp = now_ms()
    existing = await get_value(USAGE_PR_NAMESPACE, key)
    value = {
        **(existing or {}),
        "key": key,
        "thread_id": thread_id or "",
        "github_login": github_login.strip() if isinstance(github_login, str) else "",
        "user_email": user_email.strip().lower() if isinstance(user_email, str) else "",
        "owner": owner,
        "repo": repo,
        "pr_number": pr_number,
        "pr_url": pr_url or "",
        "head": head,
        "base": base,
        "additions": max(0, additions),
        "deletions": max(0, deletions),
        "changed_files": max(0, changed_files),
        "state": state or "open",
        "merged": bool(merged),
        "agent_kind": "agent",
        "updated_at_ms": stamp,
    }
    if not existing:
        value["created_at_ms"] = stamp
    elif not value.get("created_at_ms"):
        value["created_at_ms"] = existing.get("created_at_ms") or stamp
    await put_value(USAGE_PR_NAMESPACE, key, value)


async def _fetch_pr_details(
    client: httpx.AsyncClient, owner: str, repo: str, pr_number: int
) -> dict[str, Any]:
    resp = await github_request(
        client, "GET", github_url(f"/repos/{owner}/{repo}/pulls/{pr_number}")
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


def _upsert_pull_request(records: object, record: dict[str, Any]) -> list[dict[str, Any]]:
    existing = records if isinstance(records, list) else []
    repo = record.get("repo_full_name")
    number = record.get("number")
    url = record.get("url")
    return [
        item
        for item in existing
        if isinstance(item, dict)
        and not (
            item.get("repo_full_name") == repo
            and item.get("number") == number
            or item.get("url") == url
        )
    ] + [record]


async def _thread_pull_requests(thread_id: str) -> list[dict[str, Any]]:
    try:
        thread = await _client().threads.get(thread_id)
    except Exception:
        logger.debug("Failed to read existing PR metadata for thread %s", thread_id, exc_info=True)
        return []
    metadata = thread.get("metadata") if isinstance(thread, dict) else None
    if not isinstance(metadata, dict):
        return []
    records = metadata.get("pull_requests")
    if isinstance(records, list):
        return [item for item in records if isinstance(item, dict)]
    pr_url = metadata.get("pr_url")
    pr_number = metadata.get("pr_number")
    pr_ref = parse_github_pr_url(pr_url) if isinstance(pr_url, str) else None
    if not pr_ref or not isinstance(pr_number, int) or isinstance(pr_number, bool):
        return []
    return [
        {
            "repo_full_name": f"{pr_ref.owner}/{pr_ref.repo}",
            "number": pr_number,
            "url": pr_url,
            "title": metadata.get("pr_title") if isinstance(metadata.get("pr_title"), str) else "",
            "state": metadata.get("pr_state")
            if isinstance(metadata.get("pr_state"), str)
            else "open",
            "head_ref": (
                metadata.get("branch_name") if isinstance(metadata.get("branch_name"), str) else ""
            ),
            "base_ref": (
                metadata.get("base_branch")
                if isinstance(metadata.get("base_branch"), str)
                else "main"
            ),
            "author": "",
            "author_avatar_url": "",
            "created_at": "",
            "diff_stats": (
                metadata.get("diff_stats") if isinstance(metadata.get("diff_stats"), dict) else {}
            ),
        }
    ]


async def record_pr_opened(
    pr: dict[str, Any],
    *,
    configurable: dict[str, Any],
    owner: str,
    repo: str,
    head: str,
    base: str,
    token: str,
) -> None:
    """Record a just-opened (or just-found) PR for the leaderboard and the UI.

    Best-effort: telemetry must never turn a successfully opened PR into a tool
    failure, so every error is swallowed. Re-reads the PR from GitHub because
    the create response omits the diff stats the dashboard renders.
    """
    pr_number = pr.get("number")
    if not isinstance(pr_number, int):
        return
    try:
        async with github_client(token=token) as client:
            details = await _fetch_pr_details(client, owner, repo, pr_number)
        thread_id = configurable.get("thread_id")
        github_login = configurable.get("github_login")
        user_email = configurable.get("user_email")
        if not isinstance(github_login, str) or not github_login.strip():
            from .user_mappings import login_for_email

            github_login = (
                await login_for_email(user_email if isinstance(user_email, str) else None) or ""
            )
        pr_url = details.get("html_url") or pr.get("html_url")
        merged = bool(details.get("merged"))
        is_draft = bool(details.get("draft", pr.get("draft")))
        state = details.get("state") if isinstance(details.get("state"), str) else "open"
        additions_value = details.get("additions")
        additions = additions_value if isinstance(additions_value, int) else 0
        deletions_value = details.get("deletions")
        deletions = deletions_value if isinstance(deletions_value, int) else 0
        changed_files_value = details.get("changed_files")
        changed_files = changed_files_value if isinstance(changed_files_value, int) else 0
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
            repo_private = None
            base_repo = details.get("base", {}).get("repo")
            if isinstance(base_repo, dict) and isinstance(base_repo.get("private"), bool):
                repo_private = base_repo["private"]
            pr_state = derive_pr_state(state=state, merged=merged, draft=is_draft)
            pr_title = details.get("title") or pr.get("title")
            pr_user = details.get("user") or pr.get("user")
            author = pr_user.get("login") if isinstance(pr_user, dict) else None
            author_avatar_url = pr_user.get("avatar_url") if isinstance(pr_user, dict) else None
            diff_stats = {
                "files": changed_files,
                "additions": additions,
                "deletions": deletions,
            }
            record = {
                "repo_full_name": f"{owner}/{repo}",
                "number": pr_number,
                "url": pr_url if isinstance(pr_url, str) else "",
                "title": pr_title if isinstance(pr_title, str) else "",
                "state": pr_state,
                "head_ref": head,
                "base_ref": base,
                "author": author if isinstance(author, str) else "",
                "author_avatar_url": (
                    author_avatar_url if isinstance(author_avatar_url, str) else ""
                ),
                "created_at": (
                    details.get("created_at")
                    if isinstance(details.get("created_at"), str)
                    else pr.get("created_at")
                    if isinstance(pr.get("created_at"), str)
                    else ""
                ),
                "diff_stats": diff_stats,
            }
            pull_requests = _upsert_pull_request(await _thread_pull_requests(thread_id), record)
            metadata: dict[str, Any] = {
                "agent_kind": "agent",
                "pr_url": pr_url if isinstance(pr_url, str) else "",
                "pr_number": pr_number,
                "pr_state": pr_state,
                "pr_title": pr_title,
                "branch_name": head,
                "base_branch": base,
                "diff_stats": diff_stats,
                "pull_requests": pull_requests,
                "pr_urls": [
                    item["url"]
                    for item in pull_requests
                    if isinstance(item.get("url"), str) and item["url"]
                ],
            }
            if repo_private is not None:
                metadata["repo_private"] = repo_private
            await _client().threads.update(thread_id=thread_id, metadata=metadata)
    except Exception:
        logger.debug(
            "Failed to record PR usage for %s/%s#%s", owner, repo, pr_number, exc_info=True
        )
