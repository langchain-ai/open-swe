"""Dashboard API for pull requests and the coding threads that work on them."""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.github.comments import PrState
from openswe.github.http import GitHubClient
from openswe.github.pull_request_actions import (
    PullRequestAction,
    PullRequestActionResult,
    ResolveReviewThreads,
    ResolveReviewThreadsResult,
    act_on_pull_request,
    resolve_review_threads,
)
from openswe.github.pull_request_status import (
    OpenPullRequest,
    OpenPullRequests,
    list_open_pull_requests,
    pull_request_identity,
)
from openswe.github.pull_requests import PullRequest
from openswe.github.repos import accessible_repo_full_names
from openswe.threads.pr_fixes import (
    PullRequestThreadIntent,
    PullRequestThreadRun,
    PullRequestThreadStatus,
    pull_request_thread_running,
    start_pull_request_thread,
)

router = APIRouter(tags=["pull-requests"])


class PullRequestSearchResult(BaseModel):
    repo: str
    number: int
    url: str
    title: str
    body: str
    state: PrState


class PullRequestSearchResults(BaseModel):
    pull_requests: list[PullRequestSearchResult]
    has_more: bool


@router.get("/pull-requests/search")
async def api_search_pull_requests(
    q: str = Query(min_length=1, max_length=1000),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: dict[str, str] = SESSION_DEP,
) -> PullRequestSearchResults:
    accessible = await accessible_repo_full_names(session["sub"])
    rows = await PullRequest.search(
        q, repositories=sorted(accessible), limit=limit + 1, offset=offset
    )
    return PullRequestSearchResults(
        pull_requests=[
            PullRequestSearchResult(
                repo=row.repo_full_name,
                number=row.number,
                url=row.url,
                title=row.title,
                body=row.body,
                state=row.state,
            )
            for row in rows[:limit]
        ],
        has_more=len(rows) > limit,
    )


@router.get("/pull-requests")
async def api_list_pull_requests(
    repo: str = "",
    lightweight: bool = False,
    sort: Literal["created", "updated"] = "updated",
    direction: Literal["asc", "desc"] = "desc",
    page: int = 1,
    scope: Literal["mine", "review-requested"] = "mine",
    session: dict[str, Any] = SESSION_DEP,
) -> OpenPullRequests:
    async with GitHubClient.as_user(session["sub"]) as github:
        return await list_open_pull_requests(
            github,
            session["sub"],
            repo,
            lightweight=lightweight,
            sort=sort,
            direction=direction,
            page=page,
            scope=scope,
        )


@router.get("/repos/{owner}/{repo}/pulls/{number}")
async def api_pull_request_details(
    owner: str, repo: str, number: int, session: dict[str, Any] = SESSION_DEP
) -> OpenPullRequest | None:
    if pull_request_identity({"repo_full_name": f"{owner}/{repo}", "number": number}) is None:
        raise HTTPException(422, "invalid pull request")
    async with GitHubClient.as_user(session["sub"]) as github:
        return await github.repo(owner, repo).pull_request(number).load()


@router.post("/repos/{owner}/{repo}/pulls/{number}/action")
@audit_endpoint
async def api_act_on_pull_request(
    owner: str,
    repo: str,
    number: int,
    body: PullRequestAction,
    session: dict[str, Any] = SESSION_DEP,
) -> PullRequestActionResult:
    async with GitHubClient.as_user(session["sub"]) as github:
        return await act_on_pull_request(github.repo(owner, repo).pull_request(number), body)


@router.get("/repos/{owner}/{repo}/pulls/{number}/thread")
async def api_pull_request_thread_status(
    owner: str, repo: str, number: int, session: dict[str, str] = SESSION_DEP
) -> PullRequestThreadStatus:
    return await pull_request_thread_running(
        owner, repo, number, session["sub"], session.get("email")
    )


@router.post("/repos/{owner}/{repo}/pulls/{number}/thread")
@audit_endpoint
async def api_start_pull_request_thread(
    owner: str,
    repo: str,
    number: int,
    body: PullRequestThreadIntent,
    session: dict[str, str] = SESSION_DEP,
) -> PullRequestThreadRun:
    return await start_pull_request_thread(
        owner, repo, number, session["sub"], session.get("email"), intent=body
    )


@router.post("/repos/{owner}/{repo}/pulls/{number}/review-threads/resolve")
@audit_endpoint
async def api_resolve_review_threads(
    owner: str,
    repo: str,
    number: int,
    body: ResolveReviewThreads,
    session: dict[str, Any] = SESSION_DEP,
) -> ResolveReviewThreadsResult:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    async with GitHubClient.as_user(session["sub"]) as github:
        return await resolve_review_threads(github.repo(owner, repo).pull_request(number), body)
