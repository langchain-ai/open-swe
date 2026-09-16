"""Live GitHub pull-request health for dashboard threads."""

import asyncio
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

import httpx2
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from agent.github.http import (
    GITHUB_API_BASE,
    GITHUB_GRAPHQL,
    github_client,
    github_request,
)

_OWNER_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_REPO_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,100}")
_SEARCH_PAGE_SIZE = 100
# GitHub search returns at most 1000 results per query.
_SEARCH_MAX_PAGES = 10
_MERGEABILITY_ATTEMPTS = 3
_MERGEABILITY_DELAY_SECONDS = 0.7
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
_THREAD_COUNT_QUERY = """
query PullRequestThreadCount($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { isResolved }
      }
    }
  }
}
"""


CheckState = Literal["passing", "failing", "pending", "unknown", "none"]
ReviewDecision = Literal["approved", "changes_requested", "none"]


class OpenPullRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    repo: str
    number: int
    title: str
    created_at: str | None = None
    updated_at: str | None = None
    draft: bool | None = None
    details_loading: bool = False
    additions: int | None = None
    deletions: int | None = None
    mergeable: bool | None = None
    merge_state: str = "unknown"
    head_sha: str | None = None
    head_ref: str | None = None
    status_available: bool = False
    ci: CheckState = "unknown"
    review_decision: ReviewDecision | None = None
    unresolved_threads: int | None = None
    failing_checks: list[str] = Field(default_factory=list)
    pending_checks: list[str] = Field(default_factory=list)


class OpenPullRequests(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    pull_requests: list[OpenPullRequest]
    next_page: int | None
    incomplete: bool
    updated_at: str


def _as_str(value: object, default: str) -> str:
    return value if isinstance(value, str) else default


def _as_optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _as_optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def pull_request_identity(record: object) -> tuple[str, str, int] | None:
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
    client: httpx2.AsyncClient, owner: str, repo: str, number: int
) -> dict[str, Any] | None:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}"
    try:
        response = await github_request(client, "GET", url)
        response.raise_for_status()
        payload = response.json()
    except httpx2.HTTPError, ValueError:
        return None
    return payload if isinstance(payload, dict) else None


async def _fetch_mergeable_pull_request(
    client: httpx2.AsyncClient, owner: str, repo: str, number: int
) -> dict[str, Any] | None:
    """Read a pull request, waiting for GitHub to decide whether it merges.

    GitHub computes mergeability in the background and answers `null` until it
    finishes; the first read only asks it to start.
    """
    for attempt in range(_MERGEABILITY_ATTEMPTS):
        pull = await _fetch_pull_request(client, owner, repo, number)
        if pull is None or pull.get("mergeable") is not None:
            return pull
        if _live_state(pull) != "open" or attempt + 1 == _MERGEABILITY_ATTEMPTS:
            return pull
        await asyncio.sleep(_MERGEABILITY_DELAY_SECONDS * (attempt + 1))
    return None


async def _fetch_check_runs(
    client: httpx2.AsyncClient, owner: str, repo: str, sha: str
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
    except httpx2.HTTPError, ValueError:
        return None


async def _fetch_commit_statuses(
    client: httpx2.AsyncClient, owner: str, repo: str, sha: str
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
    except httpx2.HTTPError, ValueError:
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
    client: httpx2.AsyncClient, owner: str, repo: str, number: int
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
    except httpx2.HTTPError, ValueError:
        return None


async def _fetch_unresolved_thread_count(
    client: httpx2.AsyncClient, owner: str, repo: str, number: int
) -> int | None:
    """Count review threads GitHub still considers unresolved, or ``None`` if unreadable."""
    unresolved = 0
    cursor: str | None = None
    seen_cursors: set[str] = set()
    try:
        while True:
            response = await github_request(
                client,
                "POST",
                GITHUB_GRAPHQL,
                json={
                    "query": _THREAD_COUNT_QUERY,
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
            nodes = threads.get("nodes") if isinstance(threads, dict) else None
            if not isinstance(nodes, list):
                return None
            unresolved += sum(
                1
                for thread in nodes
                if isinstance(thread, Mapping) and thread.get("isResolved") is False
            )
            page_info = threads.get("pageInfo")
            if not isinstance(page_info, dict) or page_info.get("hasNextPage") is not True:
                return unresolved
            next_cursor = page_info.get("endCursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
                return None
            seen_cursors.add(next_cursor)
            cursor = next_cursor
    except httpx2.HTTPError, ValueError:
        return None


async def _pull_request_status(client: httpx2.AsyncClient, record: object) -> dict[str, Any]:
    identity = pull_request_identity(record)
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


async def _fetch_review_decision(
    client: httpx2.AsyncClient, owner: str, repo: str, number: int
) -> ReviewDecision | None:
    latest: dict[str, tuple[int, str]] = {}
    page = 1
    try:
        while True:
            response = await github_request(
                client,
                "GET",
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}/reviews",
                params={"per_page": "100", "page": str(page)},
            )
            response.raise_for_status()
            reviews = response.json()
            if not isinstance(reviews, list):
                return None
            for review in reviews:
                if not isinstance(review, dict):
                    continue
                state = review.get("state")
                user = review.get("user")
                login = user.get("login") if isinstance(user, dict) else None
                review_id = review.get("id")
                if state not in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
                    continue
                if isinstance(login, str) and isinstance(review_id, int):
                    key = login.lower()
                    if review_id > latest.get(key, (-1, ""))[0]:
                        latest[key] = (review_id, state)
            if len(reviews) < 100:
                break
            page += 1
    except httpx2.HTTPError, ValueError:
        return None
    decisions = {state for _, state in latest.values()}
    if "CHANGES_REQUESTED" in decisions:
        return "changes_requested"
    return "approved" if "APPROVED" in decisions else "none"


async def list_open_pull_requests(
    login: str,
    token: str,
    repo: str = "",
    *,
    lightweight: bool = False,
    sort: str = "updated",
    direction: str = "desc",
    page: int = 1,
) -> OpenPullRequests:
    """Read the caller's open PRs and current-head checks using their own token."""
    if not _OWNER_PATTERN.fullmatch(login):
        raise HTTPException(422, "invalid GitHub login")
    if not 1 <= page <= _SEARCH_MAX_PAGES:
        raise HTTPException(422, "invalid PR page")
    repositories = list(dict.fromkeys(repo.split(","))) if repo else []
    if any(
        pull_request_identity({"repo_full_name": name, "number": 1}) is None
        for name in repositories
    ):
        raise HTTPException(422, "repository must be owner/repo")
    if sort not in {"created", "updated"} or direction not in {"asc", "desc"}:
        raise HTTPException(422, "invalid PR sort")
    query = f"is:pr is:open author:{login}"
    for name in repositories:
        query += f" repo:{name}"
    async with github_client(token=token) as client:
        try:
            response = await github_request(
                client,
                "GET",
                f"{GITHUB_API_BASE}/search/issues",
                params={
                    "q": query,
                    "per_page": str(_SEARCH_PAGE_SIZE),
                    "page": str(page),
                    "sort": sort,
                    "order": direction,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx2.HTTPError, ValueError) as exc:
            raise HTTPException(502, "Could not load open PRs from GitHub") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise HTTPException(502, "Invalid GitHub PR search response")
        # A timed-out search answers with an arbitrary subset of the matches, so
        # any list built from it would silently hide most of a user's PRs.
        if payload.get("incomplete_results") is True:
            return OpenPullRequests(
                pull_requests=[],
                next_page=None,
                incomplete=True,
                updated_at=datetime.now(UTC).isoformat(),
            )
        semaphore = asyncio.Semaphore(4)

        async def load(item: object) -> OpenPullRequest | None:
            async with semaphore:
                return await load_open_pull_request(client, item, details=not lightweight)

        items = await asyncio.gather(*(load(item) for item in payload["items"][:_SEARCH_PAGE_SIZE]))
    total = payload.get("total_count")
    has_more = (
        isinstance(total, int) and page * _SEARCH_PAGE_SIZE < total and page < _SEARCH_MAX_PAGES
    )
    return OpenPullRequests(
        pull_requests=[item for item in items if item is not None],
        next_page=page + 1 if has_more else None,
        incomplete=payload.get("incomplete_results") is True,
        updated_at=datetime.now(UTC).isoformat(),
    )


async def load_open_pull_request(
    client: httpx2.AsyncClient, item: object, details: bool = True
) -> OpenPullRequest | None:
    if not isinstance(item, dict):
        return None
    repository_url = item.get("repository_url")
    prefix = f"{GITHUB_API_BASE}/repos/"
    full_name = item.get("repo_full_name")
    if not isinstance(full_name, str):
        full_name = (
            repository_url.removeprefix(prefix)
            if isinstance(repository_url, str) and repository_url.startswith(prefix)
            else ""
        )
    identity = pull_request_identity({"repo_full_name": full_name, "number": item.get("number")})
    if identity is None:
        return None
    owner, name, number = identity
    pull = await _fetch_mergeable_pull_request(client, owner, name, number) if details else None
    if pull is not None and _live_state(pull) != "open":
        return None
    source: Mapping[str, Any] = pull if pull is not None else item
    result = OpenPullRequest(
        repo=full_name,
        number=number,
        title=_as_str(source.get("title"), ""),
        created_at=_as_optional_str(source.get("created_at")),
        updated_at=_as_optional_str(source.get("updated_at")),
        draft=_as_optional_bool(source.get("draft")),
        details_loading=not details,
        additions=_as_optional_int(source.get("additions")),
        deletions=_as_optional_int(source.get("deletions")),
        mergeable=_as_optional_bool(source.get("mergeable")),
        merge_state=_as_str(source.get("mergeable_state"), "unknown"),
        status_available=pull is not None,
    )
    if pull is None:
        return result
    head = pull.get("head")
    if not isinstance(head, Mapping):
        return result
    result.head_ref = _as_optional_str(head.get("ref"))
    sha = _as_optional_str(head.get("sha"))
    if sha is None or not _SHA_PATTERN.fullmatch(sha):
        return result
    result.head_sha = sha
    runs, statuses, decision, unresolved_threads = await asyncio.gather(
        _fetch_check_runs(client, owner, name, sha),
        _fetch_commit_statuses(client, owner, name, sha),
        _fetch_review_decision(client, owner, name, number),
        _fetch_unresolved_thread_count(client, owner, name, number),
    )
    result.review_decision = decision
    result.unresolved_threads = unresolved_threads
    if runs is None or statuses is None:
        return result
    failed, _, _ = _normalize_checks(runs, statuses)
    failures = [_as_str(check["name"], "Unnamed check") for check in failed]
    failures.extend(
        _as_str(run.get("name"), "Unnamed check")
        for run in runs
        if run.get("status") == "completed" and run.get("conclusion") in {"cancelled", "stale"}
    )
    pending = [
        _as_str(run.get("name"), "Unnamed check")
        for run in runs
        if run.get("status") != "completed"
    ] + [
        _as_str(status.get("context"), "Unnamed status")
        for status in statuses
        if status.get("state") == "pending"
    ]
    result.failing_checks = failures
    result.pending_checks = pending
    result.ci = (
        "failing"
        if failures
        else "pending"
        if pending
        else "passing"
        if runs or statuses
        else "none"
    )
    return result
