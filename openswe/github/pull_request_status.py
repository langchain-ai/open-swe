"""Live GitHub pull-request health for dashboard threads."""

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Self

import httpx2
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from openswe.github.ci import RequiredCheck, unreported_required_checks
from openswe.github.http import GITHUB_API_BASE, GitHubClient, GraphQLError, RepoClient, or_none

logger = logging.getLogger(__name__)

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
_MERGEABILITY_QUERY = """
query PullRequestMergeability($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      mergeable
      mergeStateStatus
    }
  }
}
"""
_REVIEW_THREADS_QUERY = """
query PullRequestReviewThreads($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          path
          line
          originalLine
          comments(first: 50) {
            nodes {
              author { login }
              body: bodyText
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
      reviewDecision
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
    review_required: bool = False
    unresolved_threads: int | None = None
    failing_checks: list[str] = Field(default_factory=list)
    pending_checks: list[str] = Field(default_factory=list)
    missing_checks: list[str] = Field(default_factory=list)


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


def _thread_reply(comment: dict[str, Any]) -> dict[str, Any]:
    author = comment.get("author")
    return {
        "author": author.get("login")
        if isinstance(author, dict) and isinstance(author.get("login"), str)
        else None,
        "body": comment.get("body") if isinstance(comment.get("body"), str) else "",
        "url": comment.get("url") if isinstance(comment.get("url"), str) else None,
    }


@dataclass(frozen=True, slots=True)
class Mergeability:
    """GitHub's verdict on whether a pull request can merge."""

    mergeable: bool | None
    merge_state: str


@dataclass(frozen=True, slots=True)
class ReviewState:
    """A pull request's outstanding review work.

    ``unresolved_threads`` is ``None`` when the threads could not be read, which
    is not the same answer as none being unresolved. ``review_required`` says
    branch protection still wants an approval that the PR does not have; an
    unreadable answer leaves it false so the merge stays on offer.
    """

    unresolved_threads: int | None
    review_required: bool = False


class _CursorLoop(Exception):
    """GitHub handed back a page cursor it had already given."""


def _graphql_pull(data: Mapping[str, Any]) -> Mapping[str, Any] | None:
    repository = data.get("repository")
    pull = repository.get("pullRequest") if isinstance(repository, Mapping) else None
    return pull if isinstance(pull, Mapping) else None


def _next_cursor(connection: Mapping[str, Any], seen: set[str]) -> str | None:
    """The cursor of the next page, ``None`` on the last page."""
    page_info = connection.get("pageInfo")
    if not isinstance(page_info, Mapping) or page_info.get("hasNextPage") is not True:
        return None
    cursor = page_info.get("endCursor")
    if not isinstance(cursor, str) or not cursor or cursor in seen:
        raise _CursorLoop
    seen.add(cursor)
    return cursor


@dataclass(frozen=True, slots=True)
class PullRequestClient:
    """One pull request's reads on GitHub.

    Every read answers ``None`` (or an unknown ``ReviewState``) when GitHub
    could not answer, so one failed read degrades its part of a status rather
    than the whole of it.
    """

    repo: RepoClient
    number: int

    @classmethod
    @asynccontextmanager
    async def as_app(cls, owner: str, repo: str, number: int) -> AsyncIterator[Self]:
        """This pull request as the Open SWE GitHub App's installation on ``owner/repo`` reads it.

        Raises ``GitHubAppUnavailable`` when no installation token can be minted.
        """
        async with GitHubClient.as_app(owner, repo) as github:
            yield cls(github.repo(owner, repo), number)

    @classmethod
    def of(cls, github: GitHubClient, record: object) -> Self | None:
        """The client for a ``repo_full_name`` and ``number``, or for a search hit's ``repository_url``."""
        if not isinstance(record, Mapping):
            return None
        full_name = record.get("repo_full_name")
        repository_url = record.get("repository_url")
        prefix = f"{GITHUB_API_BASE}/repos/"
        if not isinstance(full_name, str):
            full_name = (
                repository_url.removeprefix(prefix)
                if isinstance(repository_url, str) and repository_url.startswith(prefix)
                else ""
            )
        identity = pull_request_identity(
            {"repo_full_name": full_name, "number": record.get("number")}
        )
        if identity is None:
            return None
        owner, name, number = identity
        return cls(github.repo(owner, name), number)

    @property
    def _log_extra(self) -> dict[str, object]:
        return {"pr_repo_full_name": self.repo.full_name, "pr_number": self.number}

    async def pull(self) -> dict[str, Any] | None:
        payload = await or_none(self.repo.get(f"pulls/{self.number}"))
        return payload if isinstance(payload, dict) else None

    async def head_sha(self) -> str | None:
        pull = await self.pull()
        head = pull.get("head") if pull is not None else None
        sha = head.get("sha") if isinstance(head, dict) else None
        return sha if isinstance(sha, str) and sha else None

    async def mergeable_pull(self) -> dict[str, Any] | None:
        """Read the pull request, waiting for GitHub to decide whether it merges.

        GitHub computes mergeability in the background and answers `null` until it
        finishes; the first read only asks it to start.
        """
        for attempt in range(_MERGEABILITY_ATTEMPTS):
            pull = await self.pull()
            if pull is None or pull.get("mergeable") is not None:
                return pull
            if _live_state(pull) != "open" or attempt + 1 == _MERGEABILITY_ATTEMPTS:
                return pull
            await asyncio.sleep(_MERGEABILITY_DELAY_SECONDS * (attempt + 1))
        return None

    async def reviews(self) -> list[dict[str, Any]] | None:
        return await or_none(self.repo.pages(f"pulls/{self.number}/reviews"))

    async def review_decision(self) -> ReviewDecision | None:
        """The standing decision across each reviewer's latest approval, change request or dismissal."""
        reviews = await self.reviews()
        if reviews is None:
            return None
        latest: dict[str, tuple[int, str]] = {}
        for review in reviews:
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
        decisions = {state for _, state in latest.values()}
        if "CHANGES_REQUESTED" in decisions:
            return "changes_requested"
        return "approved" if "APPROVED" in decisions else "none"

    async def mergeability(self) -> Mergeability | None:
        """Mergeability over GraphQL.

        REST answers ``mergeable: null`` whenever its cached verdict has expired,
        and only starts recomputing it; GraphQL waits for that computation, so a
        single read usually gets the real answer.
        """
        try:
            data = await self.repo.graphql(_MERGEABILITY_QUERY, {"number": self.number})
        except GraphQLError as exc:
            logger.warning(
                "Mergeability query answered with errors; falling back to what REST reported",
                extra={**self._log_extra, "graphql_errors": exc.errors},
            )
            return None
        except httpx2.HTTPError, ValueError:
            logger.warning(
                "Mergeability query failed; falling back to what REST reported",
                extra=self._log_extra,
                exc_info=True,
            )
            return None
        pull = _graphql_pull(data)
        if pull is None:
            logger.warning(
                "Mergeability query answered without a pull request", extra=self._log_extra
            )
            return None
        mergeable = pull.get("mergeable")
        merge_state = pull.get("mergeStateStatus")
        return Mergeability(
            mergeable={"MERGEABLE": True, "CONFLICTING": False}.get(
                mergeable if isinstance(mergeable, str) else ""
            ),
            merge_state=merge_state.lower() if isinstance(merge_state, str) else "",
        )

    async def unresolved_threads(self) -> list[dict[str, Any]] | None:
        unresolved: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        try:
            while True:
                data = await self.repo.graphql(
                    _REVIEW_THREADS_QUERY, {"number": self.number, "cursor": cursor}
                )
                pull = _graphql_pull(data)
                threads = pull.get("reviewThreads") if pull is not None else None
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
                            "thread_id": thread.get("id")
                            if isinstance(thread.get("id"), str)
                            else None,
                            "author": author.get("login")
                            if isinstance(author, dict) and isinstance(author.get("login"), str)
                            else None,
                            "body": comment.get("body")
                            if isinstance(comment.get("body"), str)
                            else "",
                            "path": thread.get("path")
                            if isinstance(thread.get("path"), str)
                            else "",
                            "line": line
                            if isinstance(line, int) and not isinstance(line, bool)
                            else None,
                            "url": comment.get("url")
                            if isinstance(comment.get("url"), str)
                            else None,
                            "replies": [
                                _thread_reply(reply)
                                for reply in (nodes[1:] if isinstance(nodes, list) else [])
                                if isinstance(reply, dict)
                            ],
                        }
                    )
                cursor = _next_cursor(threads, seen_cursors)
                if cursor is None:
                    return unresolved
        except _CursorLoop, httpx2.HTTPError, ValueError:
            return None

    async def review_state(self) -> ReviewState:
        """The review requirement and the number of unresolved review threads."""
        unresolved = 0
        review_required = False
        cursor: str | None = None
        seen_cursors: set[str] = set()
        try:
            while True:
                data = await self.repo.graphql(
                    _THREAD_COUNT_QUERY, {"number": self.number, "cursor": cursor}
                )
                pull = _graphql_pull(data)
                if pull is not None:
                    review_required = pull.get("reviewDecision") == "REVIEW_REQUIRED"
                threads = pull.get("reviewThreads") if pull is not None else None
                if not isinstance(threads, Mapping) or not isinstance(threads.get("nodes"), list):
                    return ReviewState(None, review_required)
                unresolved += sum(
                    1
                    for thread in threads["nodes"]
                    if isinstance(thread, Mapping) and thread.get("isResolved") is False
                )
                cursor = _next_cursor(threads, seen_cursors)
                if cursor is None:
                    return ReviewState(unresolved, review_required)
        except _CursorLoop, httpx2.HTTPError, ValueError:
            return ReviewState(None, review_required)

    async def thread_status(self) -> dict[str, Any]:
        """The PR health a dashboard thread shows: state, checks and unresolved threads."""
        result = _unavailable_pull_request(
            {"repo_full_name": self.repo.full_name, "number": self.number}
        )
        result["url"] = f"https://github.com/{self.repo.full_name}/pull/{self.number}"
        pull, review_threads = await asyncio.gather(self.pull(), self.unresolved_threads())
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
            or_none(self.repo.check_runs(sha)), or_none(self.repo.commit_statuses(sha))
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

    async def load_open(
        self, *, details: bool = True, listed: Mapping[str, Any] | None = None
    ) -> OpenPullRequest | None:
        """The open PR's live status, or ``None`` once it is closed or merged.

        Without ``details`` only ``listed`` (a search hit) is used, and nothing is read.
        """
        pull = await self.mergeable_pull() if details else None
        if pull is not None and _live_state(pull) != "open":
            return None
        source: Mapping[str, Any] = pull if pull is not None else listed or {}
        result = OpenPullRequest(
            repo=self.repo.full_name,
            number=self.number,
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
        runs, statuses, decision, review_state = await asyncio.gather(
            or_none(self.repo.check_runs(sha)),
            or_none(self.repo.commit_statuses(sha)),
            self.review_decision(),
            self.review_state(),
        )
        result.review_decision = decision
        result.review_required = review_state.review_required
        result.unresolved_threads = review_state.unresolved_threads
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
        base = pull.get("base")
        base_ref = _as_optional_str(base.get("ref")) if isinstance(base, Mapping) else None
        # A check gated on `needs:` has no run until its upstream jobs finish.
        if base_ref is not None and result.merge_state == "blocked" and not pending:
            required = await RequiredCheck.for_branch(self.repo, base_ref)
            if required is not None:
                result.missing_checks = unreported_required_checks(required, runs, statuses)
        return result


async def get_pull_request_statuses(
    github: GitHubClient, records: Sequence[object]
) -> list[dict[str, Any]]:
    """Return live status for every tracked pull request record."""
    statuses: list[dict[str, Any]] = []
    for record in records:
        client = PullRequestClient.of(github, record)
        statuses.append(
            await client.thread_status()
            if client is not None
            else _unavailable_pull_request(record)
        )
    return statuses


async def list_open_pull_requests(
    github: GitHubClient,
    login: str,
    repo: str = "",
    *,
    lightweight: bool = False,
    sort: str = "updated",
    direction: str = "desc",
    page: int = 1,
) -> OpenPullRequests:
    """Read ``login``'s open PRs and current-head checks."""
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
    try:
        payload = await github.get(
            "search/issues",
            {
                "q": query,
                "per_page": str(_SEARCH_PAGE_SIZE),
                "page": str(page),
                "sort": sort,
                "order": direction,
            },
        )
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
        client = PullRequestClient.of(github, item)
        if client is None or not isinstance(item, Mapping):
            return None
        async with semaphore:
            return await client.load_open(details=not lightweight, listed=item)

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
