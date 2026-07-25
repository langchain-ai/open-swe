"""Reconcile stale runs and eligible agent PRs that have not reached the merge queue."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from .utils.github_app import get_github_app_installation_token
from .utils.github_http import GITHUB_API_BASE, GITHUB_GRAPHQL, github_client, github_request
from .utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)
_SEARCH_PAGE_SIZE = 100
_AUTO_MERGE_ALERT_MARKER = "<!-- open-swe-auto-merge-reconcile -->"

_AUTO_MERGE_QUERY = """
query AutoMergeReconcile($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    defaultBranchRef { name }
    pullRequest(number: $number) {
      id url state isDraft baseRefName headRefName headRefOid
      mergeStateStatus isInMergeQueue
      autoMergeRequest { enabledAt }
      labels(first: 100) { nodes { name } }
      statusCheckRollup { state }
    }
  }
}
"""
_ENABLE_AUTO_MERGE = """
mutation EnableAutoMerge($pullRequestId: ID!, $expectedHeadOid: GitObjectID!) {
  enablePullRequestAutoMerge(input: {
    pullRequestId: $pullRequestId
    mergeMethod: SQUASH
    expectedHeadOid: $expectedHeadOid
  }) { pullRequest { id headRefOid autoMergeRequest { enabledAt } } }
}
"""
_DISABLE_AUTO_MERGE = """
mutation DisableAutoMerge($pullRequestId: ID!) {
  disablePullRequestAutoMerge(input: { pullRequestId: $pullRequestId }) {
    pullRequest { id autoMergeRequest { enabledAt } }
  }
}
"""
_DEQUEUE_PULL_REQUEST = """
mutation DequeuePullRequest($pullRequestId: ID!) {
  dequeuePullRequest(input: { id: $pullRequestId }) {
    pullRequest { id isInMergeQueue }
  }
}
"""


def _parse_created_at(value: Any) -> datetime | None:
    """Parse an ISO timestamp into an aware UTC datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def reconcile_stale_runs(*, max_age_seconds: int = 1800) -> dict[str, int]:
    """Cancel pending runs older than the deadline on busy threads."""
    client = langgraph_client()
    now = datetime.now(UTC)
    threads_checked = stale_runs = cancelled = 0
    offset = 0
    while True:
        try:
            threads = await client.threads.search(
                metadata=None, status="busy", limit=_SEARCH_PAGE_SIZE, offset=offset
            )
        except Exception:
            logger.exception("Reconcile sweep: thread search failed at offset %d", offset)
            break
        if not threads:
            break
        for thread in threads:
            thread_id = thread.get("thread_id") if isinstance(thread, dict) else None
            if not thread_id:
                continue
            threads_checked += 1
            try:
                runs = await client.runs.list(thread_id, status="pending")
                stale_run_ids: list[str] = []
                for run in runs:
                    created = _parse_created_at(run.get("created_at"))
                    if created is None:
                        logger.warning(
                            "Reconcile sweep: unparseable created_at on run %s (thread %s)",
                            run.get("run_id"),
                            thread_id,
                        )
                        continue
                    if (now - created).total_seconds() <= max_age_seconds:
                        continue
                    run_id = run.get("run_id")
                    if run_id:
                        stale_run_ids.append(run_id)
                if not stale_run_ids:
                    continue
                stale_runs += len(stale_run_ids)
                await client.runs.cancel_many(
                    thread_id=thread_id, run_ids=stale_run_ids, action="interrupt"
                )
                cancelled += len(stale_run_ids)
                logger.info(
                    "Reconcile sweep: cancelled %d stale pending run(s) on thread %s",
                    len(stale_run_ids),
                    thread_id,
                )
            except Exception:
                logger.exception("Reconcile sweep: failed to reconcile thread %s", thread_id)
        if len(threads) < _SEARCH_PAGE_SIZE:
            break
        offset += len(threads)
    counts = {
        "threads_checked": threads_checked,
        "stale_runs": stale_runs,
        "cancelled": cancelled,
    }
    logger.info("Reconcile sweep complete: %s", counts)
    return counts


async def _graphql(
    client: httpx.AsyncClient, query: str, variables: dict[str, Any]
) -> dict[str, Any]:
    response = await github_request(
        client, "POST", GITHUB_GRAPHQL, json={"query": query, "variables": variables}
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise RuntimeError(f"GitHub GraphQL errors: {payload['errors']}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("GitHub GraphQL response missing data")
    return data


async def _update_phase(client: Any, thread_id: str, **metadata: Any) -> None:
    await client.threads.update(thread_id=thread_id, metadata=metadata)


async def _post_alert(
    client: httpx.AsyncClient, owner: str, repo: str, number: int, reason: str
) -> None:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{number}/comments"
    page = 1
    while True:
        response = await github_request(client, "GET", url, params={"per_page": 100, "page": page})
        response.raise_for_status()
        comments = response.json()
        if isinstance(comments, list) and any(
            _AUTO_MERGE_ALERT_MARKER in str(comment.get("body", ""))
            for comment in comments
            if isinstance(comment, dict)
        ):
            return
        if not isinstance(comments, list) or len(comments) < 100:
            break
        page += 1
    body = (
        "Open SWE could not converge this eligible PR to the merge queue automatically.\n\n"
        f"Reason: `{reason}`.\n\n"
        "The reconciler did not ready, directly merge, or bypass any required check. "
        "Review the PR state, the `hold-merge` label, and repository merge-queue settings.\n\n"
        f"{_AUTO_MERGE_ALERT_MARKER}"
    )
    response = await github_request(client, "POST", url, json={"body": body})
    response.raise_for_status()


async def _collect_auto_merge_threads(client: Any) -> list[dict[str, Any]]:
    candidates = []
    offset = 0
    while True:
        page = await client.threads.search(
            metadata={"auto_merge_reconcile": True},
            limit=_SEARCH_PAGE_SIZE,
            offset=offset,
        )
        if not page:
            break
        candidates.extend(thread for thread in page if isinstance(thread, dict))
        if len(page) < _SEARCH_PAGE_SIZE:
            break
        offset += len(page)
    return candidates


async def reconcile_auto_merge_prs(
    *, queue_wait_seconds: int = 300, recovery_wait_seconds: int = 300
) -> dict[str, int]:
    langgraph = langgraph_client()
    counts = {
        "threads_checked": 0,
        "armed": 0,
        "held_disabled": 0,
        "held_dequeued": 0,
        "rearmed": 0,
        "queued": 0,
        "alerted": 0,
        "terminal": 0,
        "errors": 0,
    }
    try:
        threads = await _collect_auto_merge_threads(langgraph)
    except Exception:
        logger.exception("Auto-merge reconcile: thread search failed")
        counts["errors"] += 1
        return counts
    now = datetime.now(UTC)
    for thread in threads:
        metadata = thread.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        thread_id = thread.get("thread_id") or thread.get("id")
        owner = metadata.get("pr_owner")
        repo = metadata.get("pr_repo")
        number = metadata.get("pr_number")
        if (
            not isinstance(thread_id, str)
            or metadata.get("auto_merge_intent") is not True
            or not isinstance(owner, str)
            or not isinstance(repo, str)
            or not isinstance(number, int)
        ):
            continue
        counts["threads_checked"] += 1
        try:
            token = await get_github_app_installation_token(
                target_repo=f"{owner}/{repo}",
                repositories=[repo],
                permissions={"contents": "write", "pull_requests": "write"},
            )
            if not token:
                raise RuntimeError("GitHub App token unavailable")
            async with github_client(token=token) as github:
                data = await _graphql(
                    github,
                    _AUTO_MERGE_QUERY,
                    {"owner": owner, "repo": repo, "number": number},
                )
                repository = data.get("repository")
                repository = repository if isinstance(repository, dict) else {}
                pr = repository.get("pullRequest")
                if not isinstance(pr, dict):
                    raise RuntimeError("Pull request unavailable")
                default_ref = repository.get("defaultBranchRef")
                default_branch = default_ref.get("name") if isinstance(default_ref, dict) else None
                if not isinstance(default_branch, str) or not default_branch:
                    raise RuntimeError("Default branch unavailable")
                if pr.get("state") != "OPEN" or pr.get("baseRefName") != default_branch:
                    await _update_phase(
                        langgraph,
                        thread_id,
                        auto_merge_phase="terminal",
                        auto_merge_phase_at=now.isoformat(),
                        auto_merge_reconcile=False,
                    )
                    counts["terminal"] += 1
                    continue
                labels = pr.get("labels")
                nodes = labels.get("nodes", []) if isinstance(labels, dict) else []
                held = metadata.get("merge_hold_requested") is True or any(
                    isinstance(node, dict) and node.get("name") == "hold-merge" for node in nodes
                )
                armed = pr.get("autoMergeRequest") is not None
                queued = pr.get("isInMergeQueue") is True
                green = (pr.get("statusCheckRollup") or {}).get("state") == "SUCCESS"
                clean = pr.get("mergeStateStatus") == "CLEAN"
                pr_id = pr.get("id")
                head_sha = pr.get("headRefOid")
                phase = metadata.get("auto_merge_phase")
                phase_at = _parse_created_at(metadata.get("auto_merge_phase_at"))
                same_head = metadata.get("auto_merge_head_sha") == head_sha
                recovery_attempted = metadata.get("auto_merge_recovery_attempted") is True
                if held:
                    if queued and isinstance(pr_id, str):
                        await _graphql(github, _DEQUEUE_PULL_REQUEST, {"pullRequestId": pr_id})
                        counts["held_dequeued"] += 1
                    if armed and isinstance(pr_id, str):
                        await _graphql(github, _DISABLE_AUTO_MERGE, {"pullRequestId": pr_id})
                        counts["held_disabled"] += 1
                    await _update_phase(
                        langgraph,
                        thread_id,
                        auto_merge_phase="held",
                        auto_merge_phase_at=now.isoformat(),
                        auto_merge_head_sha=head_sha or "",
                    )
                    continue
                if queued:
                    await _update_phase(
                        langgraph,
                        thread_id,
                        auto_merge_phase="queued",
                        auto_merge_phase_at=now.isoformat(),
                        auto_merge_head_sha=head_sha or "",
                        auto_merge_reconcile=True,
                    )
                    counts["queued"] += 1
                    continue
                if pr.get("isDraft") is True:
                    if green:
                        await _post_alert(github, owner, repo, number, "green_draft")
                        await _update_phase(
                            langgraph,
                            thread_id,
                            auto_merge_phase="alerted",
                            auto_merge_phase_at=now.isoformat(),
                            auto_merge_alert_reason="green_draft",
                            auto_merge_reconcile=False,
                        )
                        counts["alerted"] += 1
                    continue
                if (
                    phase == "recovery"
                    and recovery_attempted
                    and same_head
                    and phase_at is not None
                ):
                    if (now - phase_at).total_seconds() >= recovery_wait_seconds:
                        await _post_alert(github, owner, repo, number, "queue_stall")
                        await _update_phase(
                            langgraph,
                            thread_id,
                            auto_merge_phase="alerted",
                            auto_merge_phase_at=now.isoformat(),
                            auto_merge_alert_reason="queue_stall",
                            auto_merge_reconcile=False,
                        )
                        counts["alerted"] += 1
                    continue
                if not armed:
                    if not isinstance(pr_id, str) or not isinstance(head_sha, str):
                        raise RuntimeError("PR id or head SHA unavailable")
                    await _graphql(
                        github,
                        _ENABLE_AUTO_MERGE,
                        {"pullRequestId": pr_id, "expectedHeadOid": head_sha},
                    )
                    await _update_phase(
                        langgraph,
                        thread_id,
                        auto_merge_phase="pending",
                        auto_merge_phase_at=now.isoformat(),
                        auto_merge_head_sha=head_sha,
                        auto_merge_recovery_attempted=False
                        if not same_head
                        else recovery_attempted,
                    )
                    counts["armed"] += 1
                    continue
                if not green or not clean:
                    continue
                if phase != "green" or not same_head or phase_at is None:
                    await _update_phase(
                        langgraph,
                        thread_id,
                        auto_merge_phase="green",
                        auto_merge_phase_at=now.isoformat(),
                        auto_merge_head_sha=head_sha or "",
                        auto_merge_recovery_attempted=(
                            False if not same_head else recovery_attempted
                        ),
                    )
                    continue
                if (now - phase_at).total_seconds() < queue_wait_seconds or recovery_attempted:
                    continue
                if not isinstance(pr_id, str) or not isinstance(head_sha, str):
                    raise RuntimeError("PR id or head SHA unavailable")
                await _update_phase(
                    langgraph,
                    thread_id,
                    auto_merge_phase="recovery",
                    auto_merge_phase_at=now.isoformat(),
                    auto_merge_recovery_attempted=True,
                )
                await _graphql(github, _DISABLE_AUTO_MERGE, {"pullRequestId": pr_id})
                await _graphql(
                    github,
                    _ENABLE_AUTO_MERGE,
                    {"pullRequestId": pr_id, "expectedHeadOid": head_sha},
                )
                counts["rearmed"] += 1
        except Exception:
            counts["errors"] += 1
            logger.exception("Auto-merge reconcile failed for %s/%s#%s", owner, repo, number)
    return counts
