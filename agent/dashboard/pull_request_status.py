"""Live GitHub pull-request health for dashboard threads."""

import asyncio
import re
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from agent.platforms.github.http import (
    GITHUB_API_BASE,
    GITHUB_GRAPHQL,
    github_client,
    github_request,
)

_OWNER_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_REPO_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,100}")
_SHA_PATTERN = re.compile(r"[0-9a-fA-F]{40,64}")
_FAILING_CHECK_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "action_required", "startup_failure"}
)
_INCONCLUSIVE_CHECK_CONCLUSIONS = frozenset({"cancelled", "stale", "skipped", "neutral"})
_REVIEW_THREADS_QUERY = """
query PullRequestReviewThreads($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved
          path
          line
          originalLine
          comments(first: 1) {
            nodes {
              author { login }
              body
              url
            }
          }
        }
      }
    }
  }
}
"""


def _pull_request_identity(record: object) -> tuple[str, str, int] | None:
    if not isinstance(record, Mapping):
        return None
    full_name = record.get("repo_full_name")
    number = record.get("number")
    if not isinstance(full_name, str) or full_name.count("/") != 1:
        return None
    owner, repo = full_name.split("/", 1)
    if (
        not _OWNER_PATTERN.fullmatch(owner)
        or not _REPO_PATTERN.fullmatch(repo)
        or repo in {".", ".."}
    ):
        return None
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        return None
    return owner, repo, number


def _unavailable_pull_request(record: object) -> dict[str, Any]:
    full_name = record.get("repo_full_name") if isinstance(record, Mapping) else None
    number = record.get("number") if isinstance(record, Mapping) else None
    return {
        "repoFullName": full_name if isinstance(full_name, str) else None,
        "number": number if isinstance(number, int) and not isinstance(number, bool) else None,
        "url": None,
        "statusAvailable": False,
        "state": None,
        "isDraft": None,
        "mergeConflictState": None,
        "checksAvailable": False,
        "failingChecks": [],
        "pendingCheckCount": None,
        "inconclusiveCheckCount": None,
        "commentsAvailable": False,
        "unresolvedReviewThreadCount": None,
        "unresolvedReviewThreads": [],
    }


def _merge_conflict_state(pull: Mapping[str, Any]) -> str:
    mergeable = pull.get("mergeable")
    mergeable_state = pull.get("mergeable_state")
    if mergeable is False or mergeable_state == "dirty":
        return "conflicting"
    if mergeable is True and mergeable_state == "clean":
        return "mergeable"
    return "unknown"


def _live_state(pull: Mapping[str, Any]) -> str | None:
    if pull.get("merged") is True or isinstance(pull.get("merged_at"), str):
        return "merged"
    state = pull.get("state")
    return state if state in {"open", "closed"} else None


async def _fetch_pull_request(
    client: httpx.AsyncClient, owner: str, repo: str, number: int
) -> dict[str, Any] | None:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}"
    try:
        response = await github_request(client, "GET", url)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


async def _fetch_check_runs(
    client: httpx.AsyncClient, owner: str, repo: str, sha: str
) -> list[dict[str, Any]] | None:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{sha}/check-runs"
    runs: list[dict[str, Any]] = []
    page = 1
    try:
        while True:
            response = await github_request(
                client,
                "GET",
                url,
                params={"filter": "latest", "per_page": "100", "page": str(page)},
            )
            response.raise_for_status()
            payload = response.json()
            raw_runs = payload.get("check_runs") if isinstance(payload, dict) else None
            if not isinstance(raw_runs, list):
                return None
            runs.extend(run for run in raw_runs if isinstance(run, dict))
            if len(raw_runs) < 100:
                return runs
            page += 1
    except (httpx.HTTPError, ValueError):
        return None


async def _fetch_commit_statuses(
    client: httpx.AsyncClient, owner: str, repo: str, sha: str
) -> list[dict[str, Any]] | None:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{sha}/status"
    statuses: list[dict[str, Any]] = []
    page = 1
    try:
        while True:
            response = await github_request(
                client,
                "GET",
                url,
                params={"per_page": "100", "page": str(page)},
            )
            response.raise_for_status()
            payload = response.json()
            raw_statuses = payload.get("statuses") if isinstance(payload, dict) else None
            if not isinstance(raw_statuses, list):
                return None
            statuses.extend(status for status in raw_statuses if isinstance(status, dict))
            if len(raw_statuses) < 100:
                break
            page += 1
    except (httpx.HTTPError, ValueError):
        return None
    latest: list[dict[str, Any]] = []
    contexts: set[str] = set()
    for status in statuses:
        context = status.get("context")
        if not isinstance(context, str) or context in contexts:
            continue
        contexts.add(context)
        latest.append(status)
    return latest


def _normalize_checks(
    runs: list[dict[str, Any]], statuses: list[dict[str, Any]]
) -> tuple[list[dict[str, str | None]], int, int]:
    failing: list[dict[str, str | None]] = []
    pending = 0
    inconclusive = 0
    for run in runs:
        status = run.get("status")
        conclusion = run.get("conclusion")
        if status != "completed":
            pending += 1
        elif conclusion in _FAILING_CHECK_CONCLUSIONS:
            failing.append(
                {
                    "name": run.get("name") if isinstance(run.get("name"), str) else "",
                    "conclusion": conclusion if isinstance(conclusion, str) else None,
                    "url": (
                        run.get("details_url")
                        if isinstance(run.get("details_url"), str)
                        else run.get("html_url")
                        if isinstance(run.get("html_url"), str)
                        else None
                    ),
                }
            )
        elif conclusion in _INCONCLUSIVE_CHECK_CONCLUSIONS:
            inconclusive += 1
    for status in statuses:
        state = status.get("state")
        if state == "pending":
            pending += 1
        elif state in {"failure", "error"}:
            failing.append(
                {
                    "name": (
                        status.get("context") if isinstance(status.get("context"), str) else ""
                    ),
                    "conclusion": state,
                    "url": status.get("target_url")
                    if isinstance(status.get("target_url"), str)
                    else None,
                }
            )
    return failing, pending, inconclusive


async def _fetch_unresolved_review_threads(
    client: httpx.AsyncClient, owner: str, repo: str, number: int
) -> list[dict[str, Any]] | None:
    unresolved: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    try:
        while True:
            response = await github_request(
                client,
                "POST",
                GITHUB_GRAPHQL,
                json={
                    "query": _REVIEW_THREADS_QUERY,
                    "variables": {
                        "owner": owner,
                        "repo": repo,
                        "number": number,
                        "cursor": cursor,
                    },
                },
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("errors"):
                return None
            data = payload.get("data")
            repository = data.get("repository") if isinstance(data, dict) else None
            pull = repository.get("pullRequest") if isinstance(repository, dict) else None
            threads = pull.get("reviewThreads") if isinstance(pull, dict) else None
            if not isinstance(threads, dict) or not isinstance(threads.get("nodes"), list):
                return None
            for thread in threads["nodes"]:
                if not isinstance(thread, dict) or thread.get("isResolved") is True:
                    continue
                comments = thread.get("comments")
                nodes = comments.get("nodes") if isinstance(comments, dict) else None
                comment = (
                    nodes[0]
                    if isinstance(nodes, list) and nodes and isinstance(nodes[0], dict)
                    else {}
                )
                author = comment.get("author")
                line = thread.get("line")
                if not isinstance(line, int) or isinstance(line, bool):
                    line = thread.get("originalLine")
                unresolved.append(
                    {
                        "author": author.get("login")
                        if isinstance(author, dict) and isinstance(author.get("login"), str)
                        else None,
                        "body": comment.get("body") if isinstance(comment.get("body"), str) else "",
                        "path": thread.get("path") if isinstance(thread.get("path"), str) else "",
                        "line": line
                        if isinstance(line, int) and not isinstance(line, bool)
                        else None,
                        "url": comment.get("url") if isinstance(comment.get("url"), str) else None,
                    }
                )
            page_info = threads.get("pageInfo")
            if not isinstance(page_info, dict) or page_info.get("hasNextPage") is not True:
                return unresolved
            next_cursor = page_info.get("endCursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
                return None
            seen_cursors.add(next_cursor)
            cursor = next_cursor
    except (httpx.HTTPError, ValueError):
        return None


async def _pull_request_status(client: httpx.AsyncClient, record: object) -> dict[str, Any]:
    identity = _pull_request_identity(record)
    if identity is None:
        return _unavailable_pull_request(record)
    owner, repo, number = identity
    result = _unavailable_pull_request(record)
    result.update(
        {
            "repoFullName": f"{owner}/{repo}",
            "number": number,
            "url": f"https://github.com/{owner}/{repo}/pull/{number}",
        }
    )
    pull, review_threads = await asyncio.gather(
        _fetch_pull_request(client, owner, repo, number),
        _fetch_unresolved_review_threads(client, owner, repo, number),
    )
    if review_threads is not None:
        result.update(
            {
                "commentsAvailable": True,
                "unresolvedReviewThreadCount": len(review_threads),
                "unresolvedReviewThreads": review_threads,
            }
        )
    if pull is None:
        return result
    state = _live_state(pull)
    draft = pull.get("draft")
    head = pull.get("head")
    sha = head.get("sha") if isinstance(head, dict) else None
    result.update(
        {
            "statusAvailable": state is not None and isinstance(draft, bool),
            "state": state,
            "isDraft": draft if isinstance(draft, bool) else None,
            "mergeConflictState": _merge_conflict_state(pull),
        }
    )
    if not isinstance(sha, str) or not _SHA_PATTERN.fullmatch(sha):
        return result
    runs, statuses = await asyncio.gather(
        _fetch_check_runs(client, owner, repo, sha),
        _fetch_commit_statuses(client, owner, repo, sha),
    )
    if runs is not None and statuses is not None:
        failing, pending, inconclusive = _normalize_checks(runs, statuses)
        result.update(
            {
                "checksAvailable": True,
                "failingChecks": failing,
                "pendingCheckCount": pending,
                "inconclusiveCheckCount": inconclusive,
            }
        )
    return result


async def get_pull_request_statuses(records: Sequence[object], token: str) -> list[dict[str, Any]]:
    """Return live status for every tracked pull request record."""
    async with github_client(token=token) as client:
        return [await _pull_request_status(client, record) for record in records]
