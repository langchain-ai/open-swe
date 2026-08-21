"""Reviewer output for a pull request: the list, one review, its diff, comments.

Every per-PR endpoint hangs off :data:`REPO_ACCESS`, so reaching one at all
already proves the caller can see the repository — and hands back the GitHub
token that proved it, which the comment endpoints post as.
"""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ..authz import REPO_ACCESS, SESSION, RepoAccess
from ..repo_access import accessible_repo_full_names
from ..review_api import (
    create_review_comment,
    dry_run_trace_resolution,
    get_review,
    get_review_diff,
    list_review_comments,
    list_reviews,
    proxy_pr_image,
    trigger_re_review,
    update_review_comment,
)

REVIEWS_PAGE_SIZE = 20

router = APIRouter()


@router.get("/reviews")
async def api_list_reviews(
    page: int = 0,
    mine: bool = True,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    login = session["sub"]
    accessible = await accessible_repo_full_names(login)

    async def is_accessible(summary: dict[str, Any]) -> bool:
        return summary["full_name"].lower() in accessible

    page = max(page, 0)
    reviews, has_more = await list_reviews(
        REVIEWS_PAGE_SIZE,
        offset=page * REVIEWS_PAGE_SIZE,
        author=login if mine else None,
        is_accessible=is_accessible,
    )
    return {"reviews": reviews, "page": page, "has_more": has_more}


@router.get("/reviews/{owner}/{repo}/{pr_number}")
async def api_get_review(
    owner: str,
    repo: str,
    pr_number: int,
    _access: RepoAccess = REPO_ACCESS,
) -> dict[str, Any]:
    return await get_review(owner, repo, pr_number)


@router.get("/reviews/{owner}/{repo}/{pr_number}/diff")
async def api_get_review_diff(
    owner: str,
    repo: str,
    pr_number: int,
    _access: RepoAccess = REPO_ACCESS,
) -> dict[str, Any]:
    return await get_review_diff(owner, repo, pr_number)


@router.get("/reviews/{owner}/{repo}/{pr_number}/image")
async def api_get_review_image(
    owner: str,
    repo: str,
    pr_number: int,
    url: str,
    _access: RepoAccess = REPO_ACCESS,
) -> Response:
    return await proxy_pr_image(owner, repo, pr_number, url)


@router.post("/reviews/{owner}/{repo}/{pr_number}/re-review")
async def api_re_review(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION,
    _access: RepoAccess = REPO_ACCESS,
) -> dict[str, Any]:
    return await trigger_re_review(owner, repo, pr_number, session["sub"])


@router.post("/reviews/{owner}/{repo}/{pr_number}/resolve-trace")
async def api_resolve_trace(
    owner: str,
    repo: str,
    pr_number: int,
    _access: RepoAccess = REPO_ACCESS,
) -> dict[str, Any]:
    return await dry_run_trace_resolution(owner, repo, pr_number)


class ReviewCommentCreate(BaseModel):
    path: str
    line: int
    side: Literal["LEFT", "RIGHT"]
    body: str
    start_line: int | None = None
    start_side: Literal["LEFT", "RIGHT"] | None = None


@router.get("/reviews/{owner}/{repo}/{pr_number}/comments")
async def api_list_review_comments(
    owner: str,
    repo: str,
    pr_number: int,
    _access: RepoAccess = REPO_ACCESS,
) -> dict[str, Any]:
    return await list_review_comments(owner, repo, pr_number)


@router.post("/reviews/{owner}/{repo}/{pr_number}/comments")
async def api_create_review_comment(
    owner: str,
    repo: str,
    pr_number: int,
    comment: ReviewCommentCreate,
    access: RepoAccess = REPO_ACCESS,
) -> dict[str, Any]:
    body = comment.body.strip()
    if not body:
        raise HTTPException(422, "comment body is required")
    # Post as the signed-in user (their user-to-server token), so the comment is
    # attributed to them rather than the Open SWE app.
    return await create_review_comment(
        owner,
        repo,
        pr_number,
        token=access.token,
        path=comment.path,
        line=comment.line,
        side=comment.side,
        body=body,
        start_line=comment.start_line,
        start_side=comment.start_side,
    )


class ReviewCommentUpdate(BaseModel):
    body: str


@router.patch("/reviews/{owner}/{repo}/{pr_number}/comments/{comment_id}")
async def api_update_review_comment(
    owner: str,
    repo: str,
    pr_number: int,
    comment_id: int,
    comment: ReviewCommentUpdate,
    session: dict[str, Any] = SESSION,
    access: RepoAccess = REPO_ACCESS,
) -> dict[str, Any]:
    body = comment.body.strip()
    if not body:
        raise HTTPException(422, "comment body is required")
    return await update_review_comment(
        owner,
        repo,
        pr_number,
        comment_id,
        token=access.token,
        viewer_login=session["sub"],
        body=body,
    )
