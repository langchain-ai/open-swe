"""What Open SWE did, as source events: agent runs, agent PRs, reviews, findings.

Write-only, and on the agent's critical path — a run records itself as it
starts, its PR as it opens one, and the reviewer its publications and finding
outcomes. Every record is keyed by the event it describes, so recording the
same event twice is idempotent. Reading these back into a leaderboard is the
dashboard's job (``agent.dashboard.usage_reports``), which is why the
aggregation and the legacy backfill live up there and not here: they need the
reviewer's thread kind, and the foundation must not reach into the review
domain.
"""

import asyncio
import hashlib
import json
import logging
import weakref
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
from langgraph_sdk import get_client

from ..github.api import github_client, github_request, github_url
from ..github.comments import derive_pr_state
from ..github.refs import parse_github_pr_url
from ..store import Namespace, get_value, now_ms, put_value
from ..utils.json_types import as_json_object

AGENT_RUN_NAMESPACE: list[str] = ["usage", "v2", "agent_runs"]
AGENT_PR_NAMESPACE: list[str] = ["usage", "v2", "agent_prs"]
REVIEW_NAMESPACE: list[str] = ["usage", "v2", "reviews"]
REVIEW_FINDING_NAMESPACE: list[str] = ["usage", "v2", "review_findings"]

AGENT_SOURCES = frozenset({"dashboard", "github", "slack", "linear", "schedule"})

# One lock per record per loop: two events for the same key (a PR opened and
# its webhook, say) must not interleave their read-modify-write.
_WRITE_LOCKS: weakref.WeakValueDictionary[tuple[tuple[str, ...], str, int], asyncio.Lock] = (
    weakref.WeakValueDictionary()
)

logger = logging.getLogger(__name__)


def _client():
    return get_client()


def store_key(*parts: object) -> str:
    encoded = json.dumps(parts, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def timestamp_ms(value: object) -> int:
    """Epoch milliseconds from a GitHub/ISO timestamp, epoch seconds or ms; 0 when unparseable."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int | float):
        raw = int(value)
        return raw if raw > 10_000_000_000 else raw * 1000
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.isdigit():
            return timestamp_ms(int(raw))
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return 0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return int(parsed.timestamp() * 1000)
    return 0


def normalize_login(value: object) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def normalize_email(value: object) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _int(value: object, fallback: object = 0) -> int:
    return value if isinstance(value, int) else fallback if isinstance(fallback, int) else 0


def write_lock(namespace: Namespace, key: str) -> asyncio.Lock:
    lock_key = (tuple(namespace), key, id(asyncio.get_running_loop()))
    lock = _WRITE_LOCKS.get(lock_key)
    if lock is None:
        lock = asyncio.Lock()
        _WRITE_LOCKS[lock_key] = lock
    return lock


async def _mutate(
    namespace: Namespace,
    key: str,
    update: Callable[[dict[str, Any] | None], dict[str, Any]],
) -> None:
    async with write_lock(namespace, key):
        await put_value(namespace, key, update(await get_value(namespace, key)))


async def record_agent_run_usage(
    *,
    run_id: str,
    thread_id: str,
    github_login: str | None,
    user_email: str | None,
    model_id: str,
    effort: str | None,
    source: str | None,
) -> None:
    """Record one actual Agent run, idempotently."""
    if not run_id or not thread_id:
        return
    stamp = now_ms()

    def update(existing: dict[str, Any] | None) -> dict[str, Any]:
        return existing or {
            "run_id": run_id,
            "thread_id": thread_id,
            "github_login": normalize_login(github_login),
            "user_email": normalize_email(user_email),
            "model_id": model_id,
            "effort": effort or "",
            "source": source if source in AGENT_SOURCES else "dashboard",
            "created_at_ms": stamp,
        }

    await _mutate(AGENT_RUN_NAMESPACE, store_key("run", run_id), update)


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
    created_at: object = None,
    merged_at: object = None,
) -> None:
    """Record an Agent-authored PR while preserving its original attribution."""
    if not owner or not repo or not pr_number:
        return
    key = store_key("pr", owner.lower(), repo.lower(), pr_number)
    stamp = now_ms()

    def update(existing: dict[str, Any] | None) -> dict[str, Any]:
        was_merged = bool((existing or {}).get("merged"))
        value = {
            **(existing or {}),
            "owner": owner,
            "repo": repo,
            "pr_number": pr_number,
            "pr_url": pr_url or (existing or {}).get("pr_url", ""),
            "head": head,
            "base": base,
            "additions": max(0, additions),
            "deletions": max(0, deletions),
            "changed_files": max(0, changed_files),
            "state": "closed" if was_merged else state or "open",
            "merged": was_merged or bool(merged),
            "merged_at_ms": timestamp_ms(merged_at) or (existing or {}).get("merged_at_ms", 0),
            "updated_at_ms": stamp,
        }
        if not existing:
            value.update(
                thread_id=thread_id or "",
                github_login=normalize_login(github_login),
                user_email=normalize_email(user_email),
                created_at_ms=timestamp_ms(created_at) or stamp,
            )
        return value

    await _mutate(AGENT_PR_NAMESPACE, key, update)


async def update_agent_pr_usage_from_webhook(payload: dict[str, Any]) -> None:
    """Update a known Agent PR from a verified GitHub webhook payload."""
    pr = payload.get("pull_request")
    repo_payload = payload.get("repository")
    if not isinstance(pr, dict) or not isinstance(repo_payload, dict):
        return
    owner_payload = repo_payload.get("owner")
    owner = owner_payload.get("login") if isinstance(owner_payload, dict) else None
    repo = repo_payload.get("name")
    number = pr.get("number")
    if not isinstance(owner, str) or not isinstance(repo, str) or not isinstance(number, int):
        return
    existing = await get_value(
        AGENT_PR_NAMESPACE, store_key("pr", owner.lower(), repo.lower(), number)
    )
    if not existing:
        return
    await record_agent_pr_usage(
        thread_id=existing.get("thread_id") if isinstance(existing.get("thread_id"), str) else None,
        github_login=existing.get("github_login"),
        user_email=existing.get("user_email"),
        owner=owner,
        repo=repo,
        pr_number=number,
        pr_url=pr.get("html_url"),
        head=as_json_object(pr.get("head")).get("ref") or existing.get("head", ""),
        base=as_json_object(pr.get("base")).get("ref") or existing.get("base", ""),
        additions=_int(pr.get("additions"), existing.get("additions")),
        deletions=_int(pr.get("deletions"), existing.get("deletions")),
        changed_files=_int(pr.get("changed_files"), existing.get("changed_files")),
        state=pr.get("state") if isinstance(pr.get("state"), str) else existing.get("state"),
        merged=bool(pr.get("merged")),
        created_at=pr.get("created_at"),
        merged_at=pr.get("merged_at"),
    )


def finding_surfaced(finding: Mapping[str, Any]) -> bool:
    """Whether the finding reached the PR author: posted to GitHub, or past that state."""
    surface = as_json_object(finding.get("surface"))
    return bool(
        surface.get("state") in {"surfaced", "resolve_pending", "resolved"}
        or isinstance(finding.get("github_review_id"), int)
        or isinstance(finding.get("github_review_comment_id"), int)
        or finding.get("github_review_comment_ids")
    )


def human_reply_count(finding: Mapping[str, Any]) -> int:
    interactions = finding.get("interactions")
    if isinstance(interactions, list):
        return sum(
            1
            for interaction in interactions
            if isinstance(interaction, dict) and interaction.get("kind") == "human_reply"
        )
    return int(bool(finding.get("last_human_reply_at")))


async def record_reviewer_publication(
    *,
    thread_id: str,
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    findings: Sequence[Mapping[str, Any]],
) -> None:
    """Record a completed review and its finding cohort."""
    stamp = now_ms()

    def update_review(existing: dict[str, Any] | None) -> dict[str, Any]:
        return {
            **(existing or {}),
            "thread_id": thread_id,
            "owner": owner,
            "repo": repo,
            "pr_number": pr_number,
            "head_sha": head_sha,
            "findings_recorded": sum(
                1 for finding in findings if finding.get("first_seen_sha") == head_sha
            ),
            "published_at_ms": (existing or {}).get("published_at_ms") or stamp,
        }

    await _mutate(REVIEW_NAMESPACE, store_key("review", thread_id, head_sha), update_review)
    for finding in findings:
        finding_id = finding.get("id")
        if not isinstance(finding_id, str) or not finding_id:
            continue
        surfaced = finding_surfaced(finding)

        def update(
            existing: dict[str, Any] | None,
            finding: Mapping[str, Any] = finding,
            finding_id: str = finding_id,
            surfaced: bool = surfaced,
        ) -> dict[str, Any]:
            value = {
                **(existing or {}),
                "thread_id": thread_id,
                "finding_id": finding_id,
                "owner": owner,
                "repo": repo,
                "pr_number": pr_number,
                "severity": finding.get("severity") or "",
                "category": finding.get("category") or "",
                "status": finding.get("status") or "open",
                "first_seen_sha": finding.get("first_seen_sha") or "",
                "last_confirmed_sha": finding.get("last_confirmed_sha") or "",
                "surfaced_at_ms": (existing or {}).get("surfaced_at_ms")
                or (stamp if surfaced else 0),
                "human_replies": human_reply_count(finding),
                "updated_at_ms": stamp,
            }
            if not existing:
                value["recorded_at_ms"] = stamp
            if value["status"] == "resolved" and not value.get("resolved_at_ms"):
                value["resolved_at_ms"] = stamp
                value["resolved_sha"] = value["last_confirmed_sha"]
            return value

        await _mutate(REVIEW_FINDING_NAMESPACE, store_key("finding", thread_id, finding_id), update)


async def record_reviewer_finding_state(thread_id: str, finding: Mapping[str, Any]) -> None:
    """Update an already-published finding's outcome state; unknown findings are ignored."""
    finding_id = finding.get("id")
    if not isinstance(finding_id, str) or not finding_id:
        return
    key = store_key("finding", thread_id, finding_id)
    stamp = now_ms()

    def update(existing: dict[str, Any]) -> dict[str, Any]:
        status = finding.get("status") or "open"
        value = {
            **existing,
            "status": status,
            "severity": finding.get("severity") or existing.get("severity", ""),
            "category": finding.get("category") or existing.get("category", ""),
            "last_confirmed_sha": finding.get("last_confirmed_sha") or "",
            "human_replies": human_reply_count(finding),
            "updated_at_ms": stamp,
        }
        if finding_surfaced(finding) and not value.get("surfaced_at_ms"):
            value["surfaced_at_ms"] = stamp
        if status == "resolved" and not value.get("resolved_at_ms"):
            value["resolved_at_ms"] = stamp
            value["resolved_sha"] = value["last_confirmed_sha"]
        return value

    async with write_lock(REVIEW_FINDING_NAMESPACE, key):
        existing = await get_value(REVIEW_FINDING_NAMESPACE, key)
        if existing:
            await put_value(REVIEW_FINDING_NAMESPACE, key, update(existing))


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
            created_at=details.get("created_at") or pr.get("created_at"),
            merged_at=details.get("merged_at") or pr.get("merged_at"),
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
