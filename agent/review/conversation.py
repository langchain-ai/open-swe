"""PR conversation timeline (issue comments + submitted reviews) and top-level commenting."""

import asyncio
import logging
from collections import Counter
from datetime import datetime
from typing import Annotated, Any, Literal

import httpx2
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from agent.dashboard.deps import SESSION_DEP
from agent.dashboard.repo_access import require_repo_access_for_user
from agent.github.http import GitHubClient
from agent.github.pull_request_status import PullRequestClient

logger = logging.getLogger(__name__)

router = APIRouter(tags=["review"])

_GITHUB_API = "https://api.github.com"
_PAGE_SIZE = 100

ReviewState = Literal["APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED"]


class _GitHubUser(BaseModel):
    login: str
    avatar_url: str


class _GitHubIssueComment(BaseModel):
    id: int
    user: _GitHubUser | None = None
    created_at: datetime
    body: str | None = None
    html_url: str


class _GitHubReview(BaseModel):
    id: int
    user: _GitHubUser | None = None
    state: str
    submitted_at: datetime | None = None
    body: str | None = None
    html_url: str


class _GitHubReviewComment(BaseModel):
    pull_request_review_id: int | None = None


class _GitHubError(BaseModel):
    message: str | None = None
    errors: list[dict[str, object] | str] = Field(default_factory=list)


class ConversationAuthor(BaseModel):
    login: str
    avatar_url: str


class ConversationComment(BaseModel):
    kind: Literal["comment"] = "comment"
    id: int
    author: ConversationAuthor | None
    created_at: datetime
    body: str
    html_url: str


class ConversationReview(BaseModel):
    kind: Literal["review"] = "review"
    id: int
    author: ConversationAuthor | None
    created_at: datetime
    body: str
    html_url: str
    state: ReviewState
    inline_comment_count: int


ConversationItem = Annotated[ConversationComment | ConversationReview, Field(discriminator="kind")]


class Conversation(BaseModel):
    items: list[ConversationItem]


class ConversationCommentCreate(BaseModel):
    body: str = Field(max_length=65536)


_ISSUE_COMMENTS = TypeAdapter(list[_GitHubIssueComment])
_REVIEWS = TypeAdapter(list[_GitHubReview])
_REVIEW_COMMENTS = TypeAdapter(list[_GitHubReviewComment])
_REVIEW_STATE = TypeAdapter(ReviewState)
_REVIEW_STATES: frozenset[str] = frozenset(
    ("APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED")
)


def _error_message(response: httpx2.Response) -> str:
    fallback = f"GitHub request failed ({response.status_code})"
    try:
        error = _GitHubError.model_validate(response.json())
    except ValueError, ValidationError:
        return fallback
    details = "; ".join(
        str(item["message"]) if isinstance(item, dict) and "message" in item else str(item)
        for item in error.errors
    )
    if error.message and details:
        return f"{error.message}: {details}"
    return error.message or details or fallback


async def _request(
    github: GitHubClient,
    method: Literal["GET", "POST"],
    path: str,
    *,
    params: dict[str, int] | None = None,
    body: str | None = None,
) -> httpx2.Response:
    try:
        if body is None:
            return await github.request(method, f"{_GITHUB_API}{path}", params=params)
        return await github.request(method, f"{_GITHUB_API}{path}", json={"body": body})
    except httpx2.HTTPStatusError as exc:
        response = exc.response
        message = _error_message(response)
        logger.warning(
            "GitHub conversation request failed",
            extra={
                "http_method": method,
                "github_path": path,
                "status_code": response.status_code,
                "github_message": message,
            },
        )
        status = response.status_code if response.status_code < 500 else 502
        raise HTTPException(status, message) from exc


async def _get_all_pages(github: GitHubClient, path: str) -> list[object]:
    items: list[object] = []
    page = 1
    while True:
        response = await _request(
            github, "GET", path, params={"per_page": _PAGE_SIZE, "page": page}
        )
        batch = response.json()
        if not isinstance(batch, list):
            raise HTTPException(502, "unexpected GitHub response")
        items.extend(batch)
        if len(batch) < _PAGE_SIZE:
            return items
        page += 1


def _author(user: _GitHubUser | None) -> ConversationAuthor | None:
    return ConversationAuthor(login=user.login, avatar_url=user.avatar_url) if user else None


def _comment_item(comment: _GitHubIssueComment) -> ConversationComment:
    return ConversationComment(
        id=comment.id,
        author=_author(comment.user),
        created_at=comment.created_at,
        body=comment.body or "",
        html_url=comment.html_url,
    )


def build_timeline(
    comments: list[_GitHubIssueComment],
    reviews: list[_GitHubReview],
    inline_counts: Counter[int],
) -> list[ConversationItem]:
    items: list[ConversationItem] = [_comment_item(comment) for comment in comments]
    for review in reviews:
        if review.state not in _REVIEW_STATES or review.submitted_at is None:
            continue
        items.append(
            ConversationReview(
                id=review.id,
                author=_author(review.user),
                created_at=review.submitted_at,
                body=review.body or "",
                html_url=review.html_url,
                state=_REVIEW_STATE.validate_python(review.state),
                inline_comment_count=inline_counts[review.id],
            )
        )
    items.sort(key=lambda item: (item.created_at, item.kind, item.id))
    return items


async def fetch_conversation(pull: PullRequestClient) -> Conversation:
    github, owner, repo, pr_number = pull.repo.github, pull.repo.owner, pull.repo.name, pull.number
    raw_comments, raw_reviews, raw_review_comments = await asyncio.gather(
        _get_all_pages(github, f"/repos/{owner}/{repo}/issues/{pr_number}/comments"),
        _get_all_pages(github, f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews"),
        _get_all_pages(github, f"/repos/{owner}/{repo}/pulls/{pr_number}/comments"),
    )
    try:
        comments = _ISSUE_COMMENTS.validate_python(raw_comments)
        reviews = _REVIEWS.validate_python(raw_reviews)
        review_comments = _REVIEW_COMMENTS.validate_python(raw_review_comments)
    except ValidationError as exc:
        logger.warning(
            "GitHub conversation payload invalid",
            extra={"repo_full_name": f"{owner}/{repo}", "pr_number": pr_number},
            exc_info=exc,
        )
        raise HTTPException(502, "unexpected GitHub response") from exc
    inline_counts = Counter(
        c.pull_request_review_id for c in review_comments if c.pull_request_review_id is not None
    )
    return Conversation(items=build_timeline(comments, reviews, inline_counts))


async def post_conversation_comment(pull: PullRequestClient, body: str) -> ConversationComment:
    owner, repo, pr_number = pull.repo.owner, pull.repo.name, pull.number
    response = await _request(
        pull.repo.github, "POST", f"/repos/{owner}/{repo}/issues/{pr_number}/comments", body=body
    )
    try:
        created = _GitHubIssueComment.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        logger.warning(
            "GitHub created comment payload invalid",
            extra={"repo_full_name": f"{owner}/{repo}", "pr_number": pr_number},
            exc_info=exc,
        )
        raise HTTPException(502, "unexpected GitHub response") from exc
    return _comment_item(created)


@router.get("/reviews/{owner}/{repo}/{pr_number}/conversation")
async def api_get_review_conversation(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION_DEP,
) -> Conversation:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    async with GitHubClient.as_user(session["sub"]) as github:
        return await fetch_conversation(github.repo(owner, repo).pull_request(pr_number))


@router.post("/reviews/{owner}/{repo}/{pr_number}/conversation/comments")
async def api_post_review_conversation_comment(
    owner: str,
    repo: str,
    pr_number: int,
    comment: ConversationCommentCreate,
    session: dict[str, Any] = SESSION_DEP,
) -> ConversationComment:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    body = comment.body.strip()
    if not body:
        raise HTTPException(422, "comment body is required")
    async with GitHubClient.as_user(session["sub"]) as github:
        return await post_conversation_comment(
            github.repo(owner, repo).pull_request(pr_number), body
        )
