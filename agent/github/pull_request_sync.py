"""Read a pull request out of GitHub and write it into PostgreSQL.

One sync is one full picture: the REST pull request, the check runs and legacy
commit statuses on its head sha, and every GraphQL review thread. The PR row is
stamped ``last_synced_at`` as soon as the pull request itself is readable; the
check and review-thread sets are each all-or-nothing, so a partial GitHub
outage leaves the previous set standing rather than half-deleting it.

Tokens are the caller's problem. Webhooks and the sweep pass a GitHub App
installation token (``agent.github.app.get_github_app_installation_token``);
the dashboard passes the signed-in user's token, which also bounds what the
sync is allowed to see.
"""

import asyncio
import logging
from datetime import datetime

import httpx2
from pydantic import AliasPath, BaseModel, Field

from agent.github.http import (
    GITHUB_API_BASE,
    GITHUB_GRAPHQL,
    github_client,
    github_request,
)
from agent.github.pull_request_terms import SHA_PATTERN
from agent.github.pull_requests import (
    GithubText,
    PullRequest,
    PullRequestCheck,
    PullRequestPayload,
    PullRequestReviewThread,
)

logger = logging.getLogger(__name__)

REVIEW_THREADS_QUERY = """
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

_PAGE_SIZE = 100
_MAX_PAGES = 50
_SYNC_CONCURRENCY = 4
_WRITE_BACK_CONCURRENCY = 5

_write_back_gate = asyncio.Semaphore(_WRITE_BACK_CONCURRENCY)
_write_back_tasks: set[asyncio.Task[None]] = set()


class _CheckRun(BaseModel):
    id: int
    name: GithubText = ""
    status: GithubText = ""
    conclusion: GithubText = ""
    details_url: GithubText = ""
    html_url: GithubText = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def to_check(self, head_sha: str) -> PullRequestCheck:
        return PullRequestCheck(
            head_sha=head_sha,
            kind="check_run",
            external_id=str(self.id),
            name=self.name,
            status=self.status,
            conclusion=self.conclusion,
            details_url=self.details_url or self.html_url,
            github_updated_at=self.completed_at or self.started_at,
        )


class _CheckRunPage(BaseModel):
    check_runs: list[_CheckRun] = Field(default_factory=list)


class _CommitStatus(BaseModel):
    context: GithubText = ""
    state: GithubText = ""
    target_url: GithubText = ""
    updated_at: datetime | None = None

    def to_check(self, head_sha: str) -> PullRequestCheck:
        return PullRequestCheck(
            head_sha=head_sha,
            kind="status",
            external_id=self.context,
            name=self.context,
            conclusion=self.state,
            details_url=self.target_url,
            github_updated_at=self.updated_at,
        )


class _CombinedStatus(BaseModel):
    statuses: list[_CommitStatus] = Field(default_factory=list)


class _ReviewThreadComment(BaseModel):
    author: GithubText = Field("", validation_alias=AliasPath("author", "login"))
    body: GithubText = ""
    url: GithubText = ""


class _ReviewThreadComments(BaseModel):
    nodes: list[_ReviewThreadComment] = Field(default_factory=list)


class _ReviewThreadNode(BaseModel):
    id: str
    is_resolved: bool = Field(False, validation_alias="isResolved")
    path: GithubText = ""
    line: int | None = None
    original_line: int | None = Field(None, validation_alias="originalLine")
    comments: _ReviewThreadComments = Field(default_factory=_ReviewThreadComments)

    def to_review_thread(self) -> PullRequestReviewThread:
        comment = self.comments.nodes[0] if self.comments.nodes else _ReviewThreadComment()
        return PullRequestReviewThread(
            node_id=self.id,
            is_resolved=self.is_resolved,
            path=self.path,
            line=self.line if self.line is not None else self.original_line,
            author=comment.author,
            body=comment.body,
            url=comment.url,
        )


class _PageInfo(BaseModel):
    has_next_page: bool = Field(False, validation_alias="hasNextPage")
    end_cursor: GithubText = Field("", validation_alias="endCursor")


class _ReviewThreadPage(BaseModel):
    page_info: _PageInfo = Field(default_factory=_PageInfo, validation_alias="pageInfo")
    nodes: list[_ReviewThreadNode] = Field(default_factory=list)


class _ReviewThreadsResponse(BaseModel):
    threads: _ReviewThreadPage = Field(
        validation_alias=AliasPath("data", "repository", "pullRequest", "reviewThreads")
    )


class _PullRequestRef(BaseModel):
    number: int


async def sync_pull_request(
    owner: str, repo: str, number: int, *, token: str
) -> PullRequest | None:
    """Store GitHub's current view of one pull request, or ``None`` if unreadable."""
    async with github_client(token=token) as client:
        return await _sync(client, owner, repo, number)


async def sync_review_threads(
    owner: str, repo: str, number: int, *, token: str
) -> PullRequest | None:
    """Refresh only the review threads of an already stored pull request.

    GitHub has no webhook for resolving a review thread, so review activity
    triggers this instead of a full sync.
    """
    row = await PullRequest.get(owner, repo, number)
    if row is None:
        return None
    async with github_client(token=token) as client:
        threads = await _fetch_review_threads(client, owner, repo, number)
    if threads is None:
        logger.warning(
            "Review thread refresh could not read GitHub; keeping the stored set",
            extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": number},
        )
        return row
    return await row.replace_review_threads(threads)


def schedule_pull_request_sync(owner: str, repo: str, number: int, *, token: str) -> None:
    """Refresh one pull request behind a dashboard read, never on its critical path."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_write_back(owner, repo, number, token))
    _write_back_tasks.add(task)
    task.add_done_callback(_write_back_tasks.discard)


async def _write_back(owner: str, repo: str, number: int, token: str) -> None:
    async with _write_back_gate:
        try:
            await sync_pull_request(owner, repo, number, token=token)
        except Exception:
            logger.warning(
                "Pull request write-back failed",
                extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": number},
                exc_info=True,
            )


async def sync_repository_open_pull_requests(
    owner: str, repo: str, *, token: str, limit: int | None = None
) -> list[PullRequest]:
    """Store every open pull request in ``owner/repo``, in GitHub's listing order."""
    async with github_client(token=token) as client:
        numbers = await _open_pull_request_numbers(client, owner, repo, limit)
        semaphore = asyncio.Semaphore(_SYNC_CONCURRENCY)

        async def sync_one(number: int) -> PullRequest | None:
            async with semaphore:
                return await _sync(client, owner, repo, number)

        rows = await asyncio.gather(*(sync_one(number) for number in numbers))
    return [row for row in rows if row is not None]


async def _sync(
    client: httpx2.AsyncClient, owner: str, repo: str, number: int
) -> PullRequest | None:
    payload = await _fetch_pull_request(client, owner, repo, number)
    if payload is None:
        logger.warning(
            "Pull request sync could not read the pull request",
            extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": number},
        )
        return None
    row = await payload.to_pull_request(owner, repo, number).save(
        repository_private=payload.base_repo_private, synced=True
    )
    head_sha = payload.head_sha
    checks, threads = await asyncio.gather(
        _fetch_checks(client, owner, repo, head_sha),
        _fetch_review_threads(client, owner, repo, number),
    )
    if checks is None:
        logger.warning(
            "Pull request sync could not read checks; keeping the stored set",
            extra={
                "pr_repo_full_name": f"{owner}/{repo}",
                "pr_number": number,
                "pr_head_sha": head_sha,
            },
        )
    else:
        row = await row.replace_checks(head_sha, checks)
    if threads is None:
        logger.warning(
            "Pull request sync could not read review threads; keeping the stored set",
            extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": number},
        )
    else:
        row = await row.replace_review_threads(threads)
    return row


async def _fetch_pull_request(
    client: httpx2.AsyncClient, owner: str, repo: str, number: int
) -> PullRequestPayload | None:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}"
    try:
        response = await github_request(client, "GET", url)
        response.raise_for_status()
        return PullRequestPayload.model_validate(response.json())
    except httpx2.HTTPError, ValueError:
        return None


async def _fetch_checks(
    client: httpx2.AsyncClient, owner: str, repo: str, head_sha: str
) -> list[PullRequestCheck] | None:
    """Both check kinds for ``head_sha``, or ``None`` unless every page was read."""
    if not SHA_PATTERN.fullmatch(head_sha):
        return None
    runs, statuses = await asyncio.gather(
        _fetch_check_runs(client, owner, repo, head_sha),
        _fetch_commit_statuses(client, owner, repo, head_sha),
    )
    if runs is None or statuses is None:
        return None
    return runs + statuses


async def _fetch_check_runs(
    client: httpx2.AsyncClient, owner: str, repo: str, head_sha: str
) -> list[PullRequestCheck] | None:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{head_sha}/check-runs"
    checks: list[PullRequestCheck] = []
    try:
        for page in range(1, _MAX_PAGES + 1):
            response = await github_request(
                client,
                "GET",
                url,
                params={"filter": "latest", "per_page": str(_PAGE_SIZE), "page": str(page)},
            )
            response.raise_for_status()
            runs = _CheckRunPage.model_validate(response.json()).check_runs
            checks.extend(run.to_check(head_sha) for run in runs)
            if len(runs) < _PAGE_SIZE:
                break
    except httpx2.HTTPError, ValueError:
        return None
    return checks


async def _fetch_commit_statuses(
    client: httpx2.AsyncClient, owner: str, repo: str, head_sha: str
) -> list[PullRequestCheck] | None:
    """Latest status per context; GitHub returns the newest first."""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{head_sha}/status"
    checks: dict[str, PullRequestCheck] = {}
    try:
        for page in range(1, _MAX_PAGES + 1):
            response = await github_request(
                client, "GET", url, params={"per_page": str(_PAGE_SIZE), "page": str(page)}
            )
            response.raise_for_status()
            statuses = _CombinedStatus.model_validate(response.json()).statuses
            for status in statuses:
                checks.setdefault(status.context, status.to_check(head_sha))
            if len(statuses) < _PAGE_SIZE:
                break
    except httpx2.HTTPError, ValueError:
        return None
    return list(checks.values())


async def _fetch_review_threads(
    client: httpx2.AsyncClient, owner: str, repo: str, number: int
) -> list[PullRequestReviewThread] | None:
    threads: list[PullRequestReviewThread] = []
    cursor: str | None = None
    seen: set[str] = set()
    try:
        for _ in range(_MAX_PAGES):
            response = await github_request(
                client,
                "POST",
                GITHUB_GRAPHQL,
                json={
                    "query": REVIEW_THREADS_QUERY,
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
            if isinstance(payload, dict) and payload.get("errors"):
                return None
            page = _ReviewThreadsResponse.model_validate(payload).threads
            threads.extend(node.to_review_thread() for node in page.nodes)
            if not page.page_info.has_next_page:
                return threads
            cursor = page.page_info.end_cursor
            if not cursor or cursor in seen:
                return None
            seen.add(cursor)
    except httpx2.HTTPError, ValueError:
        return None
    return None


async def _open_pull_request_numbers(
    client: httpx2.AsyncClient, owner: str, repo: str, limit: int | None
) -> list[int]:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls"
    numbers: list[int] = []
    try:
        for page in range(1, _MAX_PAGES + 1):
            response = await github_request(
                client,
                "GET",
                url,
                params={"state": "open", "per_page": str(_PAGE_SIZE), "page": str(page)},
            )
            response.raise_for_status()
            refs = [_PullRequestRef.model_validate(item) for item in response.json()]
            numbers.extend(ref.number for ref in refs)
            if len(refs) < _PAGE_SIZE or (limit is not None and len(numbers) >= limit):
                break
    except httpx2.HTTPError, ValueError, TypeError:
        logger.warning(
            "Pull request sync could not list open pull requests",
            extra={"pr_repo_full_name": f"{owner}/{repo}"},
        )
    return numbers[:limit] if limit is not None else numbers
