"""Reconcile stale runs and Mergify-owned automatic merges."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from .utils.github_app import get_github_app_installation_token
from .utils.github_http import GITHUB_API_BASE, GITHUB_GRAPHQL, github_client, github_request
from .utils.slack import GitHubPrRef
from .utils.thread_ops import langgraph_client
from .webhooks import common as webhook_common
from .webhooks import github as github_webhook

logger = logging.getLogger(__name__)
_SEARCH_PAGE_SIZE = 100
_MERGIFY_APP_SLUG = "mergify"
_MERGIFY_PROTECTIONS_CHECK = "Mergify Merge Protections"
_MERGIFY_QUEUE_CHECK = "Mergify Merge Queue"
_MERGIFY_CHECK_NAMES = {_MERGIFY_PROTECTIONS_CHECK, _MERGIFY_QUEUE_CHECK}
_QUEUE_STALL_THRESHOLD_MINUTES = 15

_AUTO_MERGE_QUERY = """
query AutoMergeReconcile($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    defaultBranchRef { name }
    pullRequest(number: $number) {
      id state isDraft baseRefName headRefOid
      labels(first: 100) { nodes { name } }
    }
  }
}
"""
_CONVERT_TO_DRAFT = """
mutation ConvertPullRequestToDraft($pullRequestId: ID!) {
  convertPullRequestToDraft(input: { pullRequestId: $pullRequestId }) {
    pullRequest { id isDraft headRefOid }
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


async def reconcile_reviewer_heads() -> dict[str, int]:
    """Recover watched pull request heads missed by synchronize delivery."""
    client = langgraph_client()
    counts = {"threads_checked": 0, "dispatched": 0, "skipped": 0, "errors": 0}
    offset = 0
    while True:
        try:
            threads = await client.threads.search(
                metadata={"kind": webhook_common.REVIEWER_THREAD_KIND, "watch": True},
                limit=_SEARCH_PAGE_SIZE,
                offset=offset,
            )
        except Exception:
            logger.exception("Reviewer head reconcile: thread search failed at offset %d", offset)
            counts["errors"] += 1
            break
        if not threads:
            break
        for thread in threads:
            metadata = thread.get("metadata") if isinstance(thread, dict) else None
            metadata = metadata if isinstance(metadata, dict) else {}
            if (
                metadata.get("kind") != webhook_common.REVIEWER_THREAD_KIND
                or metadata.get("watch") is not True
            ):
                counts["skipped"] += 1
                continue
            pr = metadata.get("pr")
            if not isinstance(pr, dict):
                counts["skipped"] += 1
                continue
            owner = pr.get("owner")
            repo = pr.get("name")
            number = pr.get("number")
            url = pr.get("url")
            if (
                not isinstance(owner, str)
                or not isinstance(repo, str)
                or not isinstance(number, int)
                or not isinstance(url, str)
            ):
                counts["skipped"] += 1
                continue
            counts["threads_checked"] += 1
            try:
                repo_config = {"owner": owner, "name": repo}
                if not await webhook_common._is_repo_auto_review_enabled(repo_config):
                    counts["skipped"] += 1
                    continue
                token, _ = await webhook_common._reviewer_token_for_repo(
                    repo_config, repo_private=None
                )
                if not token:
                    raise RuntimeError("GitHub App token unavailable")
                live_pr = await webhook_common.fetch_github_pr_metadata(
                    GitHubPrRef(owner=owner, repo=repo, number=number, url=url), token=token
                )
                if not live_pr:
                    raise RuntimeError("Pull request unavailable")
                head_sha = (live_pr.get("head") or {}).get("sha")
                if live_pr.get("state") != "open" or live_pr.get("draft") is True:
                    counts["skipped"] += 1
                    continue
                if not isinstance(head_sha, str) or not head_sha:
                    raise RuntimeError("Pull request head unavailable")
                if metadata.get("last_reviewed_sha") == head_sha:
                    counts["skipped"] += 1
                    continue
                review_start = metadata.get("review_start")
                if isinstance(review_start, dict) and review_start.get("head_sha") == head_sha:
                    counts["skipped"] += 1
                    continue
                thread_id = thread.get("thread_id") or thread.get("id")
                if isinstance(thread_id, str) and github_webhook._existing_synchronize_review_start(
                    metadata, head_sha=head_sha, thread_id=thread_id
                ):
                    counts["skipped"] += 1
                    continue
                base_repo = (live_pr.get("base") or {}).get("repo") or {}
                result = await github_webhook.process_github_pr_synchronize(
                    {
                        "repository": {
                            "owner": {"login": owner},
                            "name": repo,
                            "private": base_repo.get("private"),
                            "id": base_repo.get("id"),
                        },
                        "pull_request": live_pr,
                    }
                )
                status = result.get("status", "unknown")
                outcome = result.get("ownership") or result.get("code") or status
                if status == "accepted":
                    counts["dispatched"] += 1
                elif status == "ignored":
                    counts["skipped"] += 1
                else:
                    counts["errors"] += 1
                logger.info(
                    "Reviewer head reconcile: repository=%s/%s pr=%s head=%s outcome=%s",
                    owner,
                    repo,
                    number,
                    head_sha,
                    outcome,
                )
            except Exception:
                counts["errors"] += 1
                logger.exception(
                    "Reviewer head reconcile failed: repository=%s/%s pr=%s",
                    owner,
                    repo,
                    number,
                )
        if len(threads) < _SEARCH_PAGE_SIZE:
            break
        offset += len(threads)
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


async def _update_phase(
    client: Any,
    thread_id: str,
    *,
    current_metadata: dict[str, Any],
    phase: str,
    now: datetime,
    **metadata: Any,
) -> None:
    previous_since = current_metadata.get("auto_merge_phase_since")
    parsed_since = _parse_created_at(previous_since)
    phase_since = (
        previous_since
        if current_metadata.get("auto_merge_phase") == phase and parsed_since is not None
        else now.isoformat()
    )
    phase_metadata = {
        "auto_merge_phase": phase,
        "auto_merge_phase_at": now.isoformat(),
        "auto_merge_phase_since": phase_since,
        **metadata,
    }
    if phase != "queued" and current_metadata.get("auto_merge_alert_reason"):
        phase_metadata["auto_merge_alert_reason"] = ""
    await client.threads.update(thread_id=thread_id, metadata=phase_metadata)


def _queue_stall_threshold_minutes() -> int:
    value = os.environ.get("AUTO_MERGE_QUEUE_STALL_MINUTES")
    try:
        threshold = int(value) if value is not None else _QUEUE_STALL_THRESHOLD_MINUTES
    except (TypeError, ValueError):
        return _QUEUE_STALL_THRESHOLD_MINUTES
    return threshold if threshold > 0 else _QUEUE_STALL_THRESHOLD_MINUTES


async def _mergify_checks(
    client: httpx.AsyncClient, owner: str, repo: str, head_sha: str
) -> dict[str, dict[str, Any]]:
    response = await github_request(
        client,
        "GET",
        f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{head_sha}/check-runs",
        params={"filter": "latest", "per_page": 100},
    )
    response.raise_for_status()
    payload = response.json()
    runs = payload.get("check_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        raise RuntimeError("GitHub check-runs response missing check_runs")
    checks: dict[str, dict[str, Any]] = {}
    for run in runs:
        if not isinstance(run, dict) or run.get("name") not in _MERGIFY_CHECK_NAMES:
            continue
        app = run.get("app")
        if not isinstance(app, dict) or app.get("slug") != _MERGIFY_APP_SLUG:
            continue
        checks.setdefault(str(run["name"]), run)
    return checks


def _mergify_state(checks: dict[str, dict[str, Any]], head_sha: str) -> str:
    protections = checks.get(_MERGIFY_PROTECTIONS_CHECK)
    queue = checks.get(_MERGIFY_QUEUE_CHECK)
    if protections is None or queue is None:
        return "backend_unavailable"
    if protections.get("head_sha") != head_sha or queue.get("head_sha") != head_sha:
        return "stale_head"
    if queue.get("status") != "completed":
        return "queued"
    queue_conclusion = queue.get("conclusion")
    if queue_conclusion == "success":
        return "queued"
    if queue_conclusion != "neutral":
        return "backend_unavailable"
    if protections.get("status") != "completed":
        return "awaiting_checks"
    if protections.get("conclusion") == "success":
        return "enqueue"
    if protections.get("conclusion") is None:
        return "awaiting_checks"
    return "backend_unavailable"


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


async def reconcile_auto_merge_prs() -> dict[str, int]:
    langgraph = langgraph_client()
    counts = {
        "threads_checked": 0,
        "awaiting_checks": 0,
        "enqueue": 0,
        "queued": 0,
        "held": 0,
        "held_drafted": 0,
        "merged": 0,
        "terminal": 0,
        "backend_unavailable": 0,
        "stale_head": 0,
        "queue_stalled": 0,
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
                permissions={"checks": "read", "contents": "read", "pull_requests": "write"},
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
                state = pr.get("state")
                if state != "OPEN" or pr.get("baseRefName") != default_branch:
                    phase = "merged" if state == "MERGED" else "terminal"
                    await _update_phase(
                        langgraph,
                        thread_id,
                        current_metadata=metadata,
                        phase=phase,
                        now=now,
                        auto_merge_reconcile=False,
                    )
                    counts[phase] += 1
                    continue
                pr_id = pr.get("id")
                head_sha = pr.get("headRefOid")
                if not isinstance(pr_id, str) or not isinstance(head_sha, str):
                    raise RuntimeError("PR id or head SHA unavailable")
                labels = pr.get("labels")
                nodes = labels.get("nodes", []) if isinstance(labels, dict) else []
                held = metadata.get("merge_hold_requested") is True or any(
                    isinstance(node, dict) and node.get("name") == "hold-merge" for node in nodes
                )
                if held:
                    if pr.get("isDraft") is not True:
                        await _graphql(github, _CONVERT_TO_DRAFT, {"pullRequestId": pr_id})
                        counts["held_drafted"] += 1
                    await _update_phase(
                        langgraph,
                        thread_id,
                        current_metadata=metadata,
                        phase="held",
                        now=now,
                        auto_merge_head_sha=head_sha,
                    )
                    counts["held"] += 1
                    continue
                try:
                    checks = await _mergify_checks(github, owner, repo, head_sha)
                except Exception:
                    logger.exception(
                        "Auto-merge reconcile: Mergify checks unavailable for %s/%s#%s",
                        owner,
                        repo,
                        number,
                    )
                    await _update_phase(
                        langgraph,
                        thread_id,
                        current_metadata=metadata,
                        phase="backend_unavailable",
                        now=now,
                        auto_merge_head_sha=head_sha,
                    )
                    counts["backend_unavailable"] += 1
                    continue
                mergify_state = _mergify_state(checks, head_sha)
                if mergify_state in {"backend_unavailable", "stale_head"}:
                    await _update_phase(
                        langgraph,
                        thread_id,
                        current_metadata=metadata,
                        phase=mergify_state,
                        now=now,
                        auto_merge_head_sha=head_sha,
                    )
                    counts[mergify_state] += 1
                    continue
                alert_metadata: dict[str, str] = {}
                if mergify_state == "queued" and metadata.get("auto_merge_phase") == "queued":
                    if metadata.get("auto_merge_head_sha") != head_sha:
                        alert_metadata["auto_merge_phase_since"] = now.isoformat()
                        if metadata.get("auto_merge_alert_reason"):
                            alert_metadata["auto_merge_alert_reason"] = ""
                    else:
                        phase_since = _parse_created_at(metadata.get("auto_merge_phase_since"))
                        if phase_since is not None:
                            dwell_minutes = (now - phase_since).total_seconds() / 60
                            if dwell_minutes > _queue_stall_threshold_minutes():
                                alert_metadata = {
                                    "auto_merge_alert_reason": "queue_stall_in_queue",
                                    "auto_merge_alert_at": now.isoformat(),
                                }
                                counts["queue_stalled"] += 1
                                logger.warning(
                                    "Auto-merge reconcile: PR %s/%s#%s stalled in queue for %.1f "
                                    "minutes",
                                    owner,
                                    repo,
                                    number,
                                    dwell_minutes,
                                )
                await _update_phase(
                    langgraph,
                    thread_id,
                    current_metadata=metadata,
                    phase=mergify_state,
                    now=now,
                    auto_merge_head_sha=head_sha,
                    **alert_metadata,
                )
                counts[mergify_state] += 1
        except Exception:
            counts["errors"] += 1
            logger.exception("Auto-merge reconcile failed for %s/%s#%s", owner, repo, number)
    return counts
