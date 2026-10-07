"""PR conversation timeline (issue comments + submitted reviews) and top-level commenting."""

import asyncio
import logging
from collections import Counter
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.github.http import GitHubClient
from openswe.github.pull_request_status import PullRequestClient

logger = logging.getLogger(__name__)

router = APIRouter(tags=["review"])

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


class ConversationAuthor(BaseModel):
    login: str
    avatar_url: str

    @classmethod
    def of(cls, user: _GitHubUser | None) -> Self | None:
        return cls(login=user.login, avatar_url=user.avatar_url) if user else None


class ConversationComment(BaseModel):
    kind: Literal["comment"] = "comment"
    id: int
    author: ConversationAuthor | None
    created_at: datetime
    body: str
    html_url: str

    @classmethod
    def of(cls, comment: _GitHubIssueComment) -> Self:
        return cls(
            id=comment.id,
            author=ConversationAuthor.of(comment.user),
            created_at=comment.created_at,
            body=comment.body or "",
            html_url=comment.html_url,
        )

    @classmethod
    async def post(cls, pull: PullRequestClient, body: str) -> Self:
        created = await pull.repo.post(f"issues/{pull.number}/comments", {"body": body})
        try:
            return cls.of(_GitHubIssueComment.model_validate(created))
        except ValidationError as exc:
            logger.warning(
                "GitHub created comment payload invalid",
                extra={"repo_full_name": pull.repo.full_name, "pr_number": pull.number},
                exc_info=exc,
            )
            raise HTTPException(502, "unexpected GitHub response") from exc


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

_ISSUE_COMMENTS = TypeAdapter(list[_GitHubIssueComment])
_REVIEWS = TypeAdapter(list[_GitHubReview])
_REVIEW_COMMENTS = TypeAdapter(list[_GitHubReviewComment])
_REVIEW_STATE = TypeAdapter(ReviewState)
_REVIEW_STATES: frozenset[str] = frozenset(
    ("APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED")
)


class Conversation(BaseModel):
    items: list[ConversationItem]

    @classmethod
    def of(
        cls,
        comments: list[_GitHubIssueComment],
        reviews: list[_GitHubReview],
        inline_counts: Counter[int],
    ) -> Self:
        items: list[ConversationItem] = [ConversationComment.of(comment) for comment in comments]
        for review in reviews:
            if review.state not in _REVIEW_STATES or review.submitted_at is None:
                continue
            items.append(
                ConversationReview(
                    id=review.id,
                    author=ConversationAuthor.of(review.user),
                    created_at=review.submitted_at,
                    body=review.body or "",
                    html_url=review.html_url,
                    state=_REVIEW_STATE.validate_python(review.state),
                    inline_comment_count=inline_counts[review.id],
                )
            )
        items.sort(key=lambda item: (item.created_at, item.kind, item.id))
        return cls(items=items)

    @classmethod
    async def load(cls, pull: PullRequestClient) -> Self:
        raw_comments, raw_reviews, raw_review_comments = await asyncio.gather(
            pull.repo.pages(f"issues/{pull.number}/comments"),
            pull.repo.pages(f"pulls/{pull.number}/reviews"),
            pull.repo.pages(f"pulls/{pull.number}/comments"),
        )
        try:
            comments = _ISSUE_COMMENTS.validate_python(raw_comments)
            reviews = _REVIEWS.validate_python(raw_reviews)
            review_comments = _REVIEW_COMMENTS.validate_python(raw_review_comments)
        except ValidationError as exc:
            logger.warning(
                "GitHub conversation payload invalid",
                extra={"repo_full_name": pull.repo.full_name, "pr_number": pull.number},
                exc_info=exc,
            )
            raise HTTPException(502, "unexpected GitHub response") from exc
        inline_counts = Counter(
            c.pull_request_review_id
            for c in review_comments
            if c.pull_request_review_id is not None
        )
        return cls.of(comments, reviews, inline_counts)


class ConversationCommentCreate(BaseModel):
    body: str = Field(max_length=65536)


@router.get("/reviews/{owner}/{repo}/{pr_number}/conversation")
async def api_get_review_conversation(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION_DEP,
) -> Conversation:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    async with GitHubClient.as_user(session["sub"]) as github:
        return await Conversation.load(github.repo(owner, repo).pull_request(pr_number))


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
        return await ConversationComment.post(
            github.repo(owner, repo).pull_request(pr_number), body
        )
