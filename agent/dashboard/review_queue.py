"""Ready-for-me review queue: open PRs in the user's followed repos that are actually reviewable.

The list is fetched with the signed-in user's own OAuth token through a GitHub
search, so the authorization boundary is GitHub itself and the TTL cache is
keyed by login — a cached row for a private PR is never served to an account
that lacks access.

Changed files and per-check required-ness come from a second batched query:
asking for `files` inside a 100-result search is expensive enough that GitHub
answers HTTP 502 once the followed repos hold a few hundred open PRs, and
`isRequired` is only answerable per pull request anyway.
"""

import asyncio
import logging
import re
import time
from collections.abc import Sequence
from typing import Any, Literal, NamedTuple

import httpx2
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.dashboard.profiles import get_valid_access_token
from agent.dashboard.review_api import review_summary_for_pull
from agent.dashboard.user_data import (
    REVIEW_QUEUE_REPOS,
    ReviewQueueRepo,
    ReviewQueueRepoList,
    ReviewQueueRepos,
)
from agent.github.http import GITHUB_GRAPHQL, github_client, github_request
from agent.review.styles import normalize_repo_full_name
from agent.store import now_iso

logger = logging.getLogger(__name__)

_OWNER_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_REPO_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,100}")
_MAX_REPOS = 50
_MAX_PATHS_PER_REPO = 20
_MAX_PATH_LENGTH = 200
_FILE_PAGE_SIZE = 100
_MAX_FILE_PAGES = 10
_CONTEXT_PAGE_SIZE = 100
_DETAILS_BATCH_SIZE = 20
_CACHE_TTL_SECONDS = 60.0
_CACHE_MAX_ENTRIES = 500
_REVIEW_LOOKUP_CONCURRENCY = 10
_MORE_FILES_CONCURRENCY = 5

_PASSING_CHECK_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
_FAILING_CHECK_CONCLUSIONS = {
    "FAILURE",
    "TIMED_OUT",
    "ACTION_REQUIRED",
    "STARTUP_FAILURE",
    "CANCELLED",
}
_FAILING_STATUS_STATES = {"FAILURE", "ERROR"}

_SEARCH_PREFIX = "is:pr is:open draft:false archived:false -author:@me sort:updated-desc "

_SEARCH_QUERY = """
query ReviewQueueSearch($q: String!) {
  search(type: ISSUE, query: $q, first: 100) {
    issueCount
    nodes { ... on PullRequest {
      number title url isDraft mergeable reviewDecision updatedAt
      additions deletions changedFiles
      author { login }
      repository { nameWithOwner }
      commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
    } }
  }
}
"""

_MORE_FILES_QUERY = f"""
query ReviewQueueMoreFiles($o: String!, $r: String!, $n: Int!, $cursor: String) {{
  repository(owner:$o, name:$r) {{
    pullRequest(number:$n) {{
      files(first: {_FILE_PAGE_SIZE}, after: $cursor) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{ path }}
      }}
    }}
  }}
}}
"""

ReviewDecision = Literal["APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED"]


class ReviewQueueCounts(BaseModel):
    bugs: int = 0
    flags: int = 0


class ReviewQueueAiReview(BaseModel):
    status: Literal["running", "error", "idle"]
    counts: ReviewQueueCounts = Field(default_factory=ReviewQueueCounts)


class ReviewQueueItem(BaseModel):
    repo_full_name: str
    owner: str
    repo: str
    number: int
    title: str
    url: str
    author: str | None = None
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    review_decision: ReviewDecision | None = None
    updated_at: str
    matched_paths: list[str] = Field(default_factory=list)
    optional_failures: int = 0
    ai_review: ReviewQueueAiReview | None = None


class ReviewQueuePayload(BaseModel):
    repos: list[ReviewQueueRepo] = Field(default_factory=list)
    items: list[ReviewQueueItem] = Field(default_factory=list)
    total_open: int = 0
    fetched_at: str = Field(default_factory=now_iso)


class ReviewQueueReposBody(BaseModel):
    repos: ReviewQueueRepoList = Field(default_factory=list)


class _SearchAuthor(BaseModel):
    login: str | None = None


class _SearchRepository(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name_with_owner: str = Field(alias="nameWithOwner")


class _StatusCheckRollup(BaseModel):
    state: str | None = None


class _SearchCommit(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status_check_rollup: _StatusCheckRollup | None = Field(default=None, alias="statusCheckRollup")


class _SearchCommitNode(BaseModel):
    commit: _SearchCommit | None = None


class _SearchCommits(BaseModel):
    nodes: list[_SearchCommitNode] = Field(default_factory=list)


class _SearchFile(BaseModel):
    path: str = ""


class _PageInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    has_next_page: bool = Field(default=False, alias="hasNextPage")
    end_cursor: str | None = Field(default=None, alias="endCursor")


class _SearchFiles(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    page_info: _PageInfo = Field(default_factory=_PageInfo, alias="pageInfo")
    nodes: list[_SearchFile] = Field(default_factory=list)


class _SearchPullRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    number: int
    title: str = ""
    url: str = ""
    is_draft: bool = Field(default=False, alias="isDraft")
    mergeable: str | None = None
    review_decision: ReviewDecision | None = Field(default=None, alias="reviewDecision")
    updated_at: str = Field(default="", alias="updatedAt")
    additions: int = 0
    deletions: int = 0
    changed_files: int = Field(default=0, alias="changedFiles")
    author: _SearchAuthor | None = None
    repository: _SearchRepository
    commits: _SearchCommits = Field(default_factory=_SearchCommits)


class _SearchResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    issue_count: int = Field(default=0, alias="issueCount")
    nodes: list[dict[str, Any]] = Field(default_factory=list)


class _SearchData(BaseModel):
    search: _SearchResult = Field(default_factory=_SearchResult)


class _SearchResponse(BaseModel):
    data: _SearchData | None = None
    errors: list[Any] | None = None


class _CheckContext(BaseModel):
    """One `CheckRun` or `StatusContext` on the head commit."""

    model_config = ConfigDict(populate_by_name=True)

    typename: str = Field(default="", alias="__typename")
    name: str = ""
    status: str | None = None
    conclusion: str | None = None
    context: str = ""
    state: str | None = None
    is_required: bool = Field(default=False, alias="isRequired")


class _CheckContexts(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    total_count: int = Field(default=0, alias="totalCount")
    nodes: list[_CheckContext] = Field(default_factory=list)


class _DetailsRollup(BaseModel):
    state: str | None = None
    contexts: _CheckContexts | None = None


class _DetailsCommit(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status_check_rollup: _DetailsRollup | None = Field(default=None, alias="statusCheckRollup")


class _DetailsCommitNode(BaseModel):
    commit: _DetailsCommit | None = None


class _DetailsCommits(BaseModel):
    nodes: list[_DetailsCommitNode] = Field(default_factory=list)


class _DetailsPullRequest(BaseModel):
    files: _SearchFiles = Field(default_factory=_SearchFiles)
    commits: _DetailsCommits = Field(default_factory=_DetailsCommits)


class _DetailsRepository(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    pull_request: _DetailsPullRequest | None = Field(default=None, alias="pullRequest")


class _DetailsResponse(BaseModel):
    data: dict[str, _DetailsRepository | None] | None = None
    errors: list[Any] | None = None


class _MoreFilesData(BaseModel):
    repository: _DetailsRepository | None = None


class _MoreFilesResponse(BaseModel):
    data: _MoreFilesData | None = None
    errors: list[Any] | None = None


class _ReadyQueue(BaseModel):
    items: list[ReviewQueueItem] = Field(default_factory=list)
    total_open: int = 0


_PullKey = tuple[str, int]


class _PullNeed(NamedTuple):
    owner: str
    repo: str
    number: int
    files: bool
    contexts: bool


class _MoreFiles(NamedTuple):
    """A PR whose first page of changed files matched no configured prefix."""

    owner: str
    repo: str
    number: int
    paths: list[str]
    cursor: str | None


class _Deferred(NamedTuple):
    slot: int
    pull: _SearchPullRequest
    optional_failures: int
    more: _MoreFiles


_CacheKey = tuple[str, tuple[tuple[str, tuple[str, ...], str], ...]]

_cache: dict[_CacheKey, tuple[float, _ReadyQueue]] = {}


def _evict_expired(now: float) -> None:
    for key in [key for key, (expires, _) in _cache.items() if expires <= now]:
        _cache.pop(key, None)
    while len(_cache) > _CACHE_MAX_ENTRIES:
        _cache.pop(next(iter(_cache)), None)


def clear_cache() -> None:
    """Drop the in-process search cache (forces a refetch). Test aid."""
    _cache.clear()


def _valid_full_name(full_name: str) -> bool:
    if full_name.count("/") != 1:
        return False
    owner, repo = full_name.split("/", 1)
    return bool(
        _OWNER_PATTERN.fullmatch(owner)
        and _REPO_PATTERN.fullmatch(repo)
        and repo not in {".", ".."}
    )


async def get_review_queue_repos(login: str) -> ReviewQueueRepos:
    """The repos this user follows for their review queue (empty when never set)."""
    return await REVIEW_QUEUE_REPOS.get(login) or ReviewQueueRepos()


def _normalize_paths(full_name: str, paths: list[str]) -> list[str]:
    normalized: list[str] = []
    for raw in paths:
        path = raw.strip()
        while path.startswith(("/", "./")):
            path = path[1:] if path.startswith("/") else path[2:]
        path = path.rstrip("/")
        if not path:
            continue
        if ".." in path.split("/"):
            raise HTTPException(400, f"invalid path for {full_name}: {raw}")
        if len(path) > _MAX_PATH_LENGTH:
            raise HTTPException(400, f"paths must be at most {_MAX_PATH_LENGTH} characters")
        if path not in normalized:
            normalized.append(path)
    if len(normalized) > _MAX_PATHS_PER_REPO:
        raise HTTPException(400, f"at most {_MAX_PATHS_PER_REPO} paths per repo")
    return normalized


async def set_review_queue_repos(login: str, repos: list[ReviewQueueRepo]) -> ReviewQueueRepos:
    """Replace the followed repo list, normalized to ``owner/repo`` plus path filters."""
    normalized: list[ReviewQueueRepo] = []
    seen: set[str] = set()
    for entry in repos:
        try:
            full_name = normalize_repo_full_name(entry.full_name)
        except ValueError as exc:
            raise HTTPException(400, f"invalid repo name: {entry.full_name}") from exc
        if not _valid_full_name(full_name):
            raise HTTPException(400, f"invalid repo name: {entry.full_name}")
        if full_name.lower() in seen:
            continue
        seen.add(full_name.lower())
        normalized.append(
            ReviewQueueRepo(
                full_name=full_name,
                paths=_normalize_paths(full_name, entry.paths),
                checks=entry.checks,
            )
        )
    if len(normalized) > _MAX_REPOS:
        raise HTTPException(400, f"at most {_MAX_REPOS} repos can be followed")
    normalized.sort(key=lambda repo: repo.full_name)
    return await REVIEW_QUEUE_REPOS.put(login, ReviewQueueRepos(repos=normalized))


def _rollup_passes(pull: _SearchPullRequest) -> bool:
    head = pull.commits.nodes[0] if pull.commits.nodes else None
    commit = head.commit if head is not None else None
    if commit is None:
        return False
    rollup = commit.status_check_rollup
    # A PR with no checks configured counts as passing rather than unknown, the
    # same convention the sidebar's check dots use.
    return rollup is None or rollup.state == "SUCCESS"


def _is_mergeable(pull: _SearchPullRequest) -> bool:
    return not pull.is_draft and pull.mergeable == "MERGEABLE"


def _context_passed(context: _CheckContext) -> bool:
    if context.typename == "StatusContext":
        return context.state == "SUCCESS"
    return (context.conclusion or "") in _PASSING_CHECK_CONCLUSIONS


def _context_failed(context: _CheckContext) -> bool:
    if context.typename == "StatusContext":
        return (context.state or "") in _FAILING_STATUS_STATES
    return (context.conclusion or "") in _FAILING_CHECK_CONCLUSIONS


class _HeadChecks(NamedTuple):
    contexts: list[_CheckContext]
    truncated: bool

    def required_pass(self) -> bool:
        if self.truncated:
            return False
        return all(_context_passed(context) for context in self.contexts if context.is_required)

    def optional_failures(self) -> int:
        return sum(
            1 for context in self.contexts if not context.is_required and _context_failed(context)
        )


def _head_checks(details: _DetailsPullRequest | None) -> _HeadChecks:
    head = details.commits.nodes[0] if details and details.commits.nodes else None
    commit = head.commit if head is not None else None
    rollup = commit.status_check_rollup if commit is not None else None
    contexts = rollup.contexts if rollup is not None else None
    if contexts is None:
        return _HeadChecks([], False)
    return _HeadChecks(contexts.nodes, contexts.total_count > _CONTEXT_PAGE_SIZE)


def _matched_paths(files: _SearchFiles, paths: list[str]) -> list[str]:
    """The configured prefixes the given page of changed files touches."""
    changed = [node.path for node in files.nodes]
    return [
        path
        for path in paths
        if any(file == path or file.startswith(f"{path}/") for file in changed)
    ]


def _to_item(
    pull: _SearchPullRequest,
    matched_paths: list[str],
    optional_failures: int,
) -> ReviewQueueItem | None:
    full_name = pull.repository.name_with_owner
    if not _valid_full_name(full_name):
        return None
    owner, repo = full_name.split("/", 1)
    return ReviewQueueItem(
        repo_full_name=full_name,
        owner=owner,
        repo=repo,
        number=pull.number,
        title=pull.title,
        url=pull.url,
        author=pull.author.login if pull.author else None,
        additions=pull.additions,
        deletions=pull.deletions,
        changed_files=pull.changed_files,
        review_decision=pull.review_decision,
        updated_at=pull.updated_at,
        matched_paths=matched_paths,
        optional_failures=optional_failures,
    )


def _fetch_failed(login: str, phase: str, count: int) -> HTTPException:
    logger.warning(
        "review queue github request failed",
        extra={"login": login, "phase": phase, "count": count},
    )
    return HTTPException(502, "could not fetch pull requests from GitHub")


def _contexts_selection(index: int) -> str:
    return (
        "commits(last: 1) { nodes { commit { statusCheckRollup { state"
        f" contexts(first: {_CONTEXT_PAGE_SIZE}) {{ totalCount nodes {{ __typename"
        f" ... on CheckRun {{ name status conclusion"
        f" isRequired(pullRequestNumber: $n{index}) }}"
        f" ... on StatusContext {{ context state"
        f" isRequired(pullRequestNumber: $n{index}) }} }} }} }} }} }}"
    )


def _build_details_query(needs: Sequence[_PullNeed]) -> tuple[str, dict[str, Any]]:
    declarations: list[str] = []
    selections: list[str] = []
    variables: dict[str, Any] = {}
    for index, need in enumerate(needs):
        declarations.append(f"$o{index}:String!,$r{index}:String!,$n{index}:Int!")
        fields: list[str] = []
        if need.files:
            fields.append(
                f"files(first: {_FILE_PAGE_SIZE}) {{"
                " pageInfo { hasNextPage endCursor } nodes { path } }"
            )
        if need.contexts:
            fields.append(_contexts_selection(index))
        selections.append(
            f"p{index}: repository(owner:$o{index}, name:$r{index}) {{"
            f" pullRequest(number:$n{index}) {{ {' '.join(fields)} }} }}"
        )
        variables[f"o{index}"] = need.owner
        variables[f"r{index}"] = need.repo
        variables[f"n{index}"] = need.number
    query = f"query ReviewQueueDetails({','.join(declarations)}) {{ {' '.join(selections)} }}"
    return query, variables


def _pull_key(pull: _SearchPullRequest) -> _PullKey:
    return pull.repository.name_with_owner.lower(), pull.number


def _pull_need(pull: _SearchPullRequest, repo: ReviewQueueRepo) -> _PullNeed:
    owner, name = pull.repository.name_with_owner.split("/", 1)
    return _PullNeed(
        owner=owner,
        repo=name,
        number=pull.number,
        files=bool(repo.paths),
        contexts=repo.checks == "required",
    )


async def _fetch_details(
    login: str, needs: list[tuple[_PullKey, _PullNeed]], token: str
) -> dict[_PullKey, _DetailsPullRequest]:
    """Changed paths and head-commit checks, batched into aliased queries of 20."""
    details: dict[_PullKey, _DetailsPullRequest] = {}
    async with github_client(token=token) as client:
        for start in range(0, len(needs), _DETAILS_BATCH_SIZE):
            chunk = needs[start : start + _DETAILS_BATCH_SIZE]
            query, variables = _build_details_query([need for _, need in chunk])
            try:
                response = await github_request(
                    client,
                    "POST",
                    GITHUB_GRAPHQL,
                    json={"query": query, "variables": variables},
                )
                response.raise_for_status()
                payload = _DetailsResponse.model_validate(response.json())
            except (httpx2.HTTPError, ValueError, ValidationError) as exc:
                raise _fetch_failed(login, "details", len(needs)) from exc

            if payload.errors or payload.data is None:
                raise _fetch_failed(login, "details", len(needs))

            for index, (key, _) in enumerate(chunk):
                repository = payload.data.get(f"p{index}")
                pull_request = repository.pull_request if repository is not None else None
                if pull_request is not None:
                    details[key] = pull_request
    return details


async def _match_remaining_files(
    login: str, client: httpx2.AsyncClient, more: _MoreFiles
) -> list[str] | None:
    """Page past the first 100 changed files until a prefix matches, or give up."""
    cursor = more.cursor
    for _ in range(_MAX_FILE_PAGES):
        if cursor is None:
            return None
        variables = {
            "o": more.owner,
            "r": more.repo,
            "n": more.number,
            "cursor": cursor,
        }
        try:
            response = await github_request(
                client,
                "POST",
                GITHUB_GRAPHQL,
                json={"query": _MORE_FILES_QUERY, "variables": variables},
            )
            response.raise_for_status()
            payload = _MoreFilesResponse.model_validate(response.json())
        except (httpx2.HTTPError, ValueError, ValidationError) as exc:
            raise _fetch_failed(login, "files", 1) from exc

        if payload.errors or payload.data is None:
            raise _fetch_failed(login, "files", 1)

        repository = payload.data.repository
        pull_request = repository.pull_request if repository is not None else None
        if pull_request is None:
            return None
        matched = _matched_paths(pull_request.files, more.paths)
        if matched:
            return matched
        page = pull_request.files.page_info
        cursor = page.end_cursor if page.has_next_page else None
    return None


async def _match_all_remaining_files(
    login: str, needs: list[_MoreFiles], token: str
) -> list[list[str] | None]:
    limit = asyncio.Semaphore(_MORE_FILES_CONCURRENCY)

    async def one(client: httpx2.AsyncClient, more: _MoreFiles) -> list[str] | None:
        async with limit:
            return await _match_remaining_files(login, client, more)

    async with github_client(token=token) as client:
        return list(await asyncio.gather(*(one(client, more) for more in needs)))


async def _search_ready_items(login: str, repos: list[ReviewQueueRepo], token: str) -> _ReadyQueue:
    query = _SEARCH_PREFIX + " ".join(f"repo:{repo.full_name}" for repo in repos)
    repos_by_name = {repo.full_name.lower(): repo for repo in repos}

    def followed(key: _PullKey) -> ReviewQueueRepo:
        return repos_by_name.get(key[0]) or ReviewQueueRepo(full_name=key[0])

    try:
        async with github_client(token=token) as client:
            response = await github_request(
                client,
                "POST",
                GITHUB_GRAPHQL,
                json={"query": _SEARCH_QUERY, "variables": {"q": query}},
            )
            response.raise_for_status()
            payload = _SearchResponse.model_validate(response.json())
    except (httpx2.HTTPError, ValueError, ValidationError) as exc:
        raise _fetch_failed(login, "search", len(repos)) from exc

    if payload.errors or payload.data is None:
        raise _fetch_failed(login, "search", len(repos))

    candidates: list[_SearchPullRequest] = []
    for node in payload.data.search.nodes:
        try:
            pull = _SearchPullRequest.model_validate(node)
        except ValidationError:
            continue
        if not _is_mergeable(pull) or not _valid_full_name(pull.repository.name_with_owner):
            continue
        if followed(_pull_key(pull)).checks == "all" and not _rollup_passes(pull):
            continue
        candidates.append(pull)

    needs = [
        (_pull_key(pull), need)
        for pull in candidates
        if (need := _pull_need(pull, followed(_pull_key(pull)))).files or need.contexts
    ]
    details = await _fetch_details(login, needs, token) if needs else {}

    slots: list[ReviewQueueItem | None] = []
    deferred: list[_Deferred] = []
    for pull in candidates:
        key = _pull_key(pull)
        repo = followed(key)
        detail = details.get(key)
        checks = _head_checks(detail)
        if repo.checks == "required" and not checks.required_pass():
            continue
        files = detail.files if detail is not None else _SearchFiles()
        optional_failures = checks.optional_failures() if repo.checks == "required" else 0
        if not repo.paths:
            slots.append(_to_item(pull, [], optional_failures))
            continue
        matched = _matched_paths(files, repo.paths)
        if matched:
            slots.append(_to_item(pull, matched, optional_failures))
            continue
        if not files.page_info.has_next_page:
            continue
        owner, name = pull.repository.name_with_owner.split("/", 1)
        deferred.append(
            _Deferred(
                slot=len(slots),
                pull=pull,
                optional_failures=optional_failures,
                more=_MoreFiles(
                    owner=owner,
                    repo=name,
                    number=pull.number,
                    paths=repo.paths,
                    cursor=files.page_info.end_cursor,
                ),
            )
        )
        slots.append(None)

    if deferred:
        resolved = await _match_all_remaining_files(
            login, [entry.more for entry in deferred], token
        )
        for entry, matched in zip(deferred, resolved, strict=True):
            if matched:
                slots[entry.slot] = _to_item(entry.pull, matched, entry.optional_failures)

    items = [item for item in slots if item is not None]
    return _ReadyQueue(items=items, total_open=payload.data.search.issue_count)


async def _ready_items(login: str, repos: list[ReviewQueueRepo], token: str) -> _ReadyQueue:
    now = time.monotonic()
    _evict_expired(now)
    key: _CacheKey = (
        login,
        tuple((repo.full_name, tuple(repo.paths), repo.checks) for repo in repos),
    )
    cached = _cache.get(key)
    if cached and cached[0] > now:
        return cached[1]
    ready = await _search_ready_items(login, repos, token)
    _cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, ready)
    return ready


async def _ai_review(item: ReviewQueueItem) -> ReviewQueueAiReview | None:
    summary = await review_summary_for_pull(item.owner, item.repo, item.number)
    if summary is None:
        return None
    try:
        return ReviewQueueAiReview.model_validate(summary)
    except ValidationError:
        return None


async def _ai_reviews(items: list[ReviewQueueItem]) -> list[ReviewQueueAiReview | None]:
    limit = asyncio.Semaphore(_REVIEW_LOOKUP_CONCURRENCY)

    async def one(item: ReviewQueueItem) -> ReviewQueueAiReview | None:
        async with limit:
            return await _ai_review(item)

    return list(await asyncio.gather(*(one(item) for item in items)))


async def get_review_queue(login: str) -> ReviewQueuePayload:
    """Ready-to-review PRs across the user's followed repos, newest activity first."""
    record = await get_review_queue_repos(login)
    if not record.repos:
        return ReviewQueuePayload()

    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")

    ready = await _ready_items(login, record.repos, token)
    reviews = await _ai_reviews(ready.items)
    return ReviewQueuePayload(
        repos=record.repos,
        items=[
            item.model_copy(update={"ai_review": review})
            for item, review in zip(ready.items, reviews, strict=True)
        ],
        total_open=ready.total_open,
    )
