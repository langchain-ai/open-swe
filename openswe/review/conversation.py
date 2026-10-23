"""A pull request's conversation on GitHub: timeline, inline threads, and the viewer's replies."""

import logging
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.database import postgres
from openswe.github.http import GitHubClient
from openswe.github.mirror import conversation as mirrored
from openswe.github.mirror.ingest import (
    remember_comment,
    remember_review_comment,
    remember_thread_resolution,
)
from openswe.github.mirror.sync import PullRequestSync
from openswe.github.mirror.views import avatar_url
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.repos import ReadableRepositories

logger = logging.getLogger(__name__)

router = APIRouter(tags=["review"])

# Hidden markers (Open SWE's finding ids, bot metadata) are not part of what a person wrote.
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# Open SWE stamps everything it publishes, including what it posts with a person's token.
_OPEN_SWE_MARKER_RE = re.compile(r"<!--\s*open-swe-review(?:er|-comment)\b")
# Open SWE's GitHub footers point back at this page or ask for GitHub reactions; here they're noise.
_OPEN_SWE_FOOTER_RES = (
    re.compile(r"-{3,}\s*\n\*Your feedback helps Open SWE learn\.[^\n]*\*"),
    re.compile(r"^React 👍 or 👎.*$", re.MULTILINE),
    re.compile(r"\[Open in Web\]\([^)]*\)(?:\s*•\s*)?"),
)
_BLANK_RUN_RE = re.compile(r"\n{3,}")

ReviewState = Literal["APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED"]
DiffSide = Literal["LEFT", "RIGHT"]


class _GitHubUser(BaseModel):
    login: str
    avatar_url: str
    type: str = "User"


class _GitHubIssueComment(BaseModel):
    id: int
    user: _GitHubUser | None = None
    created_at: datetime
    body: str | None = None
    html_url: str


class _GitHubReviewComment(BaseModel):
    id: int
    pull_request_review_id: int | None = None
    in_reply_to_id: int | None = None
    user: _GitHubUser | None = None
    created_at: datetime
    body: str | None = None
    html_url: str
    path: str
    diff_hunk: str = ""
    line: int | None = None
    original_line: int | None = None
    start_line: int | None = None
    side: DiffSide | None = None


@contextmanager
def _expected_shape(pull: PullRequestClient) -> Iterator[None]:
    """GitHub answering with an unexpected shape is a 502, logged with the pull request."""
    try:
        yield
    except ValidationError as exc:
        logger.warning(
            "GitHub conversation payload invalid",
            extra={"repo_full_name": pull.repo.full_name, "pr_number": pull.number},
            exc_info=exc,
        )
        raise HTTPException(502, "unexpected GitHub response") from exc


def _display_body(body: str | None) -> str:
    text = body or ""
    if _OPEN_SWE_MARKER_RE.search(text):
        for footer in _OPEN_SWE_FOOTER_RES:
            text = footer.sub("", text)
    return _BLANK_RUN_RE.sub("\n\n", _HTML_COMMENT_RE.sub("", text)).strip()


class ConversationAuthor(BaseModel):
    login: str
    avatar_url: str
    bot: bool

    # GitHub's account type only: a body marker is anyone's to paste, so it never makes a bot.
    @classmethod
    def of(cls, user: _GitHubUser | None) -> Self | None:
        if user is None:
            return None
        return cls(login=user.login, avatar_url=user.avatar_url, bot=user.type == "Bot")

    @classmethod
    def stored(cls, login: str, github_id: int | None, account_type: str) -> Self | None:
        if not login:
            return None
        return cls(
            login=login,
            avatar_url=avatar_url(github_id) or f"https://github.com/{login}.png",
            bot=account_type == "Bot",
        )


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
            body=_display_body(comment.body),
            html_url=comment.html_url,
        )

    @classmethod
    def stored(cls, comment: mirrored.IssueComment) -> Self:
        return cls(
            id=comment.github_id,
            author=ConversationAuthor.stored(
                comment.author_login, comment.author_github_id, comment.author_type
            ),
            created_at=_created(comment.github_created_at, comment.created_at),
            body=_display_body(comment.body),
            html_url=comment.html_url,
        )

    @classmethod
    async def post(cls, pull: PullRequestClient, body: str) -> Self:
        created = await pull.comment(body)
        await remember_comment(pull.repo.owner, pull.repo.name, pull.number, created)
        with _expected_shape(pull):
            return cls.of(_GitHubIssueComment.model_validate(created))


class ConversationReview(BaseModel):
    kind: Literal["review"] = "review"
    id: int
    author: ConversationAuthor | None
    created_at: datetime
    body: str
    html_url: str
    state: ReviewState


class ConversationCommit(BaseModel):
    kind: Literal["commit"] = "commit"
    sha: str
    author: ConversationAuthor | None
    created_at: datetime
    message: str
    html_url: str


ConversationItem = Annotated[
    ConversationComment | ConversationReview | ConversationCommit, Field(discriminator="kind")
]


class ThreadComment(BaseModel):
    id: int
    # The review this comment was submitted with; a reply is its own review on GitHub.
    review_id: int | None
    author: ConversationAuthor | None
    created_at: datetime
    body: str
    html_url: str

    @classmethod
    def of(cls, comment: _GitHubReviewComment) -> Self:
        return cls(
            id=comment.id,
            review_id=comment.pull_request_review_id,
            author=ConversationAuthor.of(comment.user),
            created_at=comment.created_at,
            body=_display_body(comment.body),
            html_url=comment.html_url,
        )

    @classmethod
    def stored(cls, comment: mirrored.ReviewComment) -> Self:
        return cls(
            id=comment.github_id,
            review_id=comment.review_github_id,
            author=ConversationAuthor.stored(
                comment.author_login, comment.author_github_id, comment.author_type
            ),
            created_at=_created(comment.github_created_at, comment.created_at),
            body=_display_body(comment.body),
            html_url=comment.html_url,
        )

    @classmethod
    async def reply(cls, pull: PullRequestClient, comment_id: int, body: str) -> Self:
        created = await pull.reply_to_review_comment(comment_id, body)
        await remember_review_comment(pull.repo.owner, pull.repo.name, pull.number, created)
        with _expected_shape(pull):
            return cls.of(_GitHubReviewComment.model_validate(created))


class ReviewThread(BaseModel):
    """An inline thread: its first comment's anchor, then every reply in order."""

    id: int
    node_id: str | None
    path: str
    line: int | None
    start_line: int | None
    side: DiffSide
    original_line: int | None
    diff_hunk: str
    outdated: bool
    resolved: bool
    comments: list[ThreadComment]

    @classmethod
    def group(
        cls,
        review_comments: Sequence[mirrored.ReviewComment],
        states: Mapping[int, mirrored.ReviewThread],
    ) -> list[Self]:
        """Group inline comments into threads; a reply points at its thread's first comment."""
        ordered = sorted(
            review_comments,
            key=lambda c: (_created(c.github_created_at, c.created_at), c.github_id),
        )
        replies: dict[int, list[mirrored.ReviewComment]] = {}
        for comment in ordered:
            if comment.in_reply_to_github_id is not None:
                replies.setdefault(comment.in_reply_to_github_id, []).append(comment)
        threads: list[Self] = []
        for root in ordered:
            if root.in_reply_to_github_id is not None:
                continue
            state = states.get(root.github_id)
            threads.append(
                cls(
                    id=root.github_id,
                    node_id=(state.node_id or None) if state else None,
                    path=root.path,
                    line=root.line,
                    start_line=root.start_line,
                    side="LEFT" if root.side == "LEFT" else "RIGHT",
                    original_line=root.original_line,
                    diff_hunk=root.diff_hunk,
                    outdated=state.outdated if state else root.line is None,
                    resolved=state.resolved if state else False,
                    comments=[
                        ThreadComment.stored(c) for c in [root, *replies.get(root.github_id, [])]
                    ],
                )
            )
        return threads


class Conversation(BaseModel):
    items: list[ConversationItem]
    threads: list[ReviewThread]

    @classmethod
    async def stored(cls, pull_request_id: UUID) -> Self:
        """The conversation as the mirror holds it; pending reviews never reach the mirror."""
        async with postgres.session() as session:
            comments = await session.scalars(
                select(mirrored.IssueComment).where(
                    mirrored.IssueComment.pull_request_id == pull_request_id
                )
            )
            reviews = await session.scalars(
                select(mirrored.Review).where(mirrored.Review.pull_request_id == pull_request_id)
            )
            review_comments = list(
                await session.scalars(
                    select(mirrored.ReviewComment).where(
                        mirrored.ReviewComment.pull_request_id == pull_request_id
                    )
                )
            )
            commits = await session.scalars(
                select(mirrored.PullRequestCommit).where(
                    mirrored.PullRequestCommit.pull_request_id == pull_request_id
                )
            )
            threads = await session.scalars(
                select(mirrored.ReviewThread).where(
                    mirrored.ReviewThread.pull_request_id == pull_request_id
                )
            )
        items: list[ConversationItem] = [ConversationComment.stored(c) for c in comments]
        items.extend(
            ConversationReview(
                id=review.github_id,
                author=ConversationAuthor.stored(
                    review.author_login, review.author_github_id, "User"
                ),
                created_at=review.submitted_at,
                body=_display_body(review.body),
                html_url=review.html_url,
                state=_REVIEW_STATES[review.state],
            )
            for review in reviews
            if review.submitted_at is not None
        )
        items.extend(
            ConversationCommit(
                sha=commit.sha,
                author=ConversationAuthor.stored(
                    commit.author_login, commit.author_github_id, commit.author_type
                ),
                created_at=commit.authored_at,
                message=commit.message,
                html_url=commit.html_url,
            )
            for commit in commits
            if commit.authored_at is not None
        )
        items.sort(key=lambda item: item.created_at)
        return cls(
            items=items,
            threads=ReviewThread.group(
                review_comments, {t.root_comment_github_id: t for t in threads}
            ),
        )


def _created(github: datetime | None, stored: datetime | None) -> datetime:
    return github or stored or datetime.fromtimestamp(0, UTC)


_REVIEW_STATES: Mapping[mirrored.ReviewState, ReviewState] = {
    "approved": "APPROVED",
    "changes_requested": "CHANGES_REQUESTED",
    "commented": "COMMENTED",
    "dismissed": "DISMISSED",
}


class ConversationCommentCreate(BaseModel):
    body: str = Field(max_length=65536)

    @property
    def text(self) -> str:
        stripped = self.body.strip()
        if not stripped:
            raise HTTPException(422, "comment body is required")
        return stripped


class ThreadResolution(BaseModel):
    resolved: bool


@router.get("/reviews/{owner}/{repo}/{pr_number}/conversation")
async def api_get_review_conversation(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION_DEP,
) -> Conversation:
    (await ReadableRepositories.of(session["sub"])).require(f"{owner}/{repo}")
    stored = await PullRequestSync(owner, repo, pr_number).mirrored()
    if stored is None:
        raise HTTPException(404, "pull request not found")
    return await Conversation.stored(stored.id)


@router.post("/reviews/{owner}/{repo}/{pr_number}/conversation/comments")
@audit_endpoint
async def api_post_review_conversation_comment(
    owner: str,
    repo: str,
    pr_number: int,
    comment: ConversationCommentCreate,
    session: dict[str, Any] = SESSION_DEP,
) -> ConversationComment:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    body = comment.text
    async with GitHubClient.as_user(session["sub"]) as github:
        return await ConversationComment.post(
            github.repo(owner, repo).pull_request(pr_number), body
        )


@router.post("/reviews/{owner}/{repo}/{pr_number}/threads/{comment_id}/replies")
@audit_endpoint
async def api_reply_to_review_thread(
    owner: str,
    repo: str,
    pr_number: int,
    comment_id: int,
    comment: ConversationCommentCreate,
    session: dict[str, Any] = SESSION_DEP,
) -> ThreadComment:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    body = comment.text
    async with GitHubClient.as_user(session["sub"]) as github:
        return await ThreadComment.reply(
            github.repo(owner, repo).pull_request(pr_number), comment_id, body
        )


@router.put(
    "/reviews/{owner}/{repo}/{pr_number}/threads/{thread_node_id}/resolution", status_code=204
)
@audit_endpoint
async def api_set_review_thread_resolution(
    owner: str,
    repo: str,
    pr_number: int,
    thread_node_id: str,
    resolution: ThreadResolution,
    session: dict[str, Any] = SESSION_DEP,
) -> None:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    async with GitHubClient.as_user(session["sub"]) as github:
        await (
            github.repo(owner, repo)
            .pull_request(pr_number)
            .set_thread_resolved(thread_node_id, resolution.resolved)
        )
    await remember_thread_resolution(thread_node_id, resolved=resolution.resolved)
