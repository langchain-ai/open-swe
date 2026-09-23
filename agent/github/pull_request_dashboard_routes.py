"""Dashboard API for pull requests and the coding threads that work on them."""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException

from agent.dashboard.deps import SESSION_DEP
from agent.dashboard.profiles import get_valid_access_token
from agent.dashboard.repo_access import require_repo_access_for_user
from agent.github.http import github_client
from agent.github.pull_request_actions import (
    PullRequestAction,
    PullRequestActionResult,
    ResolveReviewThreads,
    ResolveReviewThreadsResult,
    act_on_pull_request,
    resolve_review_threads,
)
from agent.github.pull_request_status import (
    OpenPullRequest,
    OpenPullRequests,
    list_open_pull_requests,
    load_open_pull_request,
    pull_request_identity,
)
from agent.threads.pr_fixes import (
    PullRequestThreadIntent,
    PullRequestThreadRun,
    PullRequestThreadStatus,
    pull_request_thread_running,
    start_pull_request_thread,
)

router = APIRouter(tags=["pull-requests"])


@router.get("/pull-requests")
async def api_list_pull_requests(
    repo: str = "",
    lightweight: bool = False,
    sort: Literal["created", "updated"] = "updated",
    direction: Literal["asc", "desc"] = "desc",
    page: int = 1,
    scope: Literal["mine"] = "mine",
    session: dict[str, Any] = SESSION_DEP,
) -> OpenPullRequests:
    token = await get_valid_access_token(session["sub"])
    if not token:
        raise HTTPException(401, "GitHub token unavailable, re-login required")
    return await list_open_pull_requests(
        session["sub"],
        token,
        repo,
        lightweight=lightweight,
        sort=sort,
        direction=direction,
        page=page,
    )


@router.get("/repos/{owner}/{repo}/pulls/{number}")
async def api_pull_request_details(
    owner: str, repo: str, number: int, session: dict[str, Any] = SESSION_DEP
) -> OpenPullRequest | None:
    if pull_request_identity({"repo_full_name": f"{owner}/{repo}", "number": number}) is None:
        raise HTTPException(422, "invalid pull request")
    token = await get_valid_access_token(session["sub"])
    if not token:
        raise HTTPException(401, "GitHub token unavailable, re-login required")
    async with github_client(token=token) as client:
        return await load_open_pull_request(
            client, {"repo_full_name": f"{owner}/{repo}", "number": number}
        )


@router.post("/repos/{owner}/{repo}/pulls/{number}/action")
async def api_act_on_pull_request(
    owner: str,
    repo: str,
    number: int,
    body: PullRequestAction,
    session: dict[str, Any] = SESSION_DEP,
) -> PullRequestActionResult:
    token = await get_valid_access_token(session["sub"])
    if not token:
        raise HTTPException(401, "GitHub token unavailable, re-login required")
    return await act_on_pull_request(owner, repo, number, body, token)


@router.get("/repos/{owner}/{repo}/pulls/{number}/thread")
async def api_pull_request_thread_status(
    owner: str, repo: str, number: int, session: dict[str, str] = SESSION_DEP
) -> PullRequestThreadStatus:
    return await pull_request_thread_running(
        owner, repo, number, session["sub"], session.get("email")
    )


@router.post("/repos/{owner}/{repo}/pulls/{number}/thread")
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
async def api_resolve_review_threads(
    owner: str,
    repo: str,
    number: int,
    body: ResolveReviewThreads,
    session: dict[str, Any] = SESSION_DEP,
) -> ResolveReviewThreadsResult:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    token = await get_valid_access_token(session["sub"])
    if not token:
        raise HTTPException(401, "GitHub token unavailable, re-login required")
    return await resolve_review_threads(owner, repo, number, body, token)
