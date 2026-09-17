"""Dashboard API for the signed-in user's own open pull requests."""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException

from agent.dashboard.deps import SESSION_DEP
from agent.dashboard.profiles import get_valid_access_token
from agent.github.http import github_client
from agent.github.pull_request_actions import (
    PullRequestAction,
    PullRequestActionResult,
    RepositoryMergeMethods,
    act_on_pull_request,
    repository_merge_methods,
)
from agent.github.pull_request_status import (
    OpenPullRequest,
    OpenPullRequests,
    list_open_pull_requests,
    load_open_pull_request,
    pull_request_identity,
)

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


@router.post("/my-pull-requests/{owner}/{repo}/{number}/action")
async def api_act_on_my_pull_request(
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
