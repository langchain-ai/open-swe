"""Dashboard API for the signed-in user's own open pull requests."""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException

from agent.dashboard.deps import SESSION_DEP
from agent.dashboard.profiles import get_valid_access_token
from agent.github.close_pull_request import ClosePullRequestResult, close_pull_request
from agent.github.http import github_client
from agent.github.merge_pull_request import (
    MergePullRequestRequest,
    MergePullRequestResult,
    RepositoryMergeMethods,
    merge_pull_request,
    repository_merge_methods,
)
from agent.github.pull_request_status import (
    OpenPullRequest,
    OpenPullRequests,
    list_open_pull_requests,
    load_open_pull_request,
    pull_request_identity,
)
from agent.github.ready_pull_request import ReadyPullRequestResult, mark_pull_request_ready

router = APIRouter(tags=["pull-requests"])


@router.get("/my-pull-requests")
async def api_list_my_pull_requests(
    repo: str = "",
    lightweight: bool = False,
    sort: Literal["created", "updated"] = "updated",
    direction: Literal["asc", "desc"] = "desc",
    page: int = 1,
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


# Declared before the ``{number}`` route so the literal path segment wins.
@router.get("/my-pull-requests/{owner}/{repo}/merge-methods")
async def api_my_pull_request_merge_methods(
    owner: str, repo: str, session: dict[str, Any] = SESSION_DEP
) -> RepositoryMergeMethods:
    token = await get_valid_access_token(session["sub"])
    if not token:
        raise HTTPException(401, "GitHub token unavailable, re-login required")
    return await repository_merge_methods(owner, repo, token)


@router.get("/my-pull-requests/{owner}/{repo}/{number}")
async def api_my_pull_request_details(
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


@router.post("/my-pull-requests/{owner}/{repo}/{number}/merge")
async def api_merge_my_pull_request(
    owner: str,
    repo: str,
    number: int,
    body: MergePullRequestRequest,
    session: dict[str, Any] = SESSION_DEP,
) -> MergePullRequestResult:
    token = await get_valid_access_token(session["sub"])
    if not token:
        raise HTTPException(401, "GitHub token unavailable, re-login required")
    return await merge_pull_request(owner, repo, number, body, token)


@router.post("/my-pull-requests/{owner}/{repo}/{number}/close")
async def api_close_my_pull_request(
    owner: str, repo: str, number: int, session: dict[str, Any] = SESSION_DEP
) -> ClosePullRequestResult:
    token = await get_valid_access_token(session["sub"])
    if not token:
        raise HTTPException(401, "GitHub token unavailable, re-login required")
    return await close_pull_request(owner, repo, number, token)


@router.post("/my-pull-requests/{owner}/{repo}/{number}/ready")
async def api_ready_my_pull_request(
    owner: str, repo: str, number: int, session: dict[str, Any] = SESSION_DEP
) -> ReadyPullRequestResult:
    token = await get_valid_access_token(session["sub"])
    if not token:
        raise HTTPException(401, "GitHub token unavailable, re-login required")
    return await mark_pull_request_ready(owner, repo, number, token)
