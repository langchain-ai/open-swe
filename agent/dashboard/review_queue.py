"""Ready-for-me review queue: open PRs in the user's followed repos that are actually reviewable.

The list is fetched with the signed-in user's own OAuth token through a single
GitHub search, so the authorization boundary is GitHub itself and the TTL cache
is keyed by login — a cached row for a private PR is never served to an account
that lacks access.
"""

import asyncio
import logging
import re
import time
from typing import Any, Literal

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
_CACHE_TTL_SECONDS = 60.0
_CACHE_MAX_ENTRIES = 500
_REVIEW_LOOKUP_CONCURRENCY = 10

_SEARCH_PREFIX = "is:pr is:open draft:false archived:false -author:@me sort:updated-desc "

_SEARCH_QUERY = """
query ReviewQueueSearch($q: String!) {
  search(type: ISSUE, query: $q, first: 100) {
    nodes { ... on PullRequest {
      number title url isDraft mergeable reviewDecision updatedAt
      additions deletions changedFiles
      files(first: 100) { totalCount nodes { path } }
      author { login }
      repository { nameWithOwner }
      commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
    } }
  }
}
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
    files_truncated: bool = False
    ai_review: ReviewQueueAiReview | None = None


class ReviewQueuePayload(BaseModel):
    repos: list[ReviewQueueRepo] = Field(default_factory=list)
    items: list[ReviewQueueItem] = Field(default_factory=list)
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


class _SearchFiles(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    total_count: int = Field(default=0, alias="totalCount")
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
    files: _SearchFiles = Field(default_factory=_SearchFiles)


class _SearchResult(BaseModel):
    nodes: list[dict[str, Any]] = Field(default_factory=list)


class _SearchData(BaseModel):
    search: _SearchResult = Field(default_factory=_SearchResult)


class _SearchResponse(BaseModel):
    data: _SearchData | None = None
    errors: list[Any] | None = None


_CacheKey = tuple[str, tuple[tuple[str, tuple[str, ...]], ...]]

_cache: dict[_CacheKey, tuple[float, list[ReviewQueueItem]]] = {}


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
            ReviewQueueRepo(full_name=full_name, paths=_normalize_paths(full_name, entry.paths))
        )
    if len(normalized) > _MAX_REPOS:
        raise HTTPException(400, f"at most {_MAX_REPOS} repos can be followed")
    normalized.sort(key=lambda repo: repo.full_name)
    return await REVIEW_QUEUE_REPOS.put(login, ReviewQueueRepos(repos=normalized))


def _checks_pass(pull: _SearchPullRequest) -> bool:
    head = pull.commits.nodes[0] if pull.commits.nodes else None
    commit = head.commit if head is not None else None
    if commit is None:
        return False
    rollup = commit.status_check_rollup
    # A PR with no checks configured counts as passing rather than unknown, the
    # same convention the sidebar's check dots use.
    return rollup is None or rollup.state == "SUCCESS"


def _is_ready(pull: _SearchPullRequest) -> bool:
    return not pull.is_draft and pull.mergeable == "MERGEABLE" and _checks_pass(pull)


def _path_match(pull: _SearchPullRequest, paths: list[str]) -> tuple[list[str], bool] | None:
    """The configured prefixes this PR touches, or ``None`` when it touches none."""
    if not paths:
        return [], False
    changed = [node.path for node in pull.files.nodes]
    matched = [
        path
        for path in paths
        if any(file == path or file.startswith(f"{path}/") for file in changed)
    ]
    if matched:
        return matched, False
    if pull.files.total_count > _FILE_PAGE_SIZE:
        return [], True
    return None


def _to_item(
    pull: _SearchPullRequest, matched_paths: list[str], files_truncated: bool
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
        files_truncated=files_truncated,
    )


async def _search_ready_items(
    login: str, repos: list[ReviewQueueRepo], token: str
) -> list[ReviewQueueItem]:
    query = _SEARCH_PREFIX + " ".join(f"repo:{repo.full_name}" for repo in repos)
    paths_by_repo = {repo.full_name.lower(): repo.paths for repo in repos}
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
        logger.warning(
            "review queue search request failed",
            extra={"login": login, "repo_count": len(repos)},
        )
        raise HTTPException(502, "could not fetch pull requests from GitHub") from exc

    if payload.errors or payload.data is None:
        logger.warning(
            "review queue search returned errors",
            extra={"login": login, "repo_count": len(repos)},
        )
        raise HTTPException(502, "could not fetch pull requests from GitHub")

    items: list[ReviewQueueItem] = []
    for node in payload.data.search.nodes:
        try:
            pull = _SearchPullRequest.model_validate(node)
        except ValidationError:
            continue
        if not _is_ready(pull):
            continue
        match = _path_match(pull, paths_by_repo.get(pull.repository.name_with_owner.lower(), []))
        if match is None:
            continue
        item = _to_item(pull, *match)
        if item is not None:
            items.append(item)
    return items


async def _ready_items(
    login: str, repos: list[ReviewQueueRepo], token: str
) -> list[ReviewQueueItem]:
    now = time.monotonic()
    _evict_expired(now)
    key: _CacheKey = (login, tuple((repo.full_name, tuple(repo.paths)) for repo in repos))
    cached = _cache.get(key)
    if cached and cached[0] > now:
        return cached[1]
    items = await _search_ready_items(login, repos, token)
    _cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, items)
    return items


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

    items = await _ready_items(login, record.repos, token)
    reviews = await _ai_reviews(items)
    return ReviewQueuePayload(
        repos=record.repos,
        items=[
            item.model_copy(update={"ai_review": review})
            for item, review in zip(items, reviews, strict=True)
        ],
    )
