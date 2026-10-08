"""A pull request's conversation on GitHub: timeline, inline threads, and the viewer's replies."""

import asyncio
import logging
import re
from collections import Counter
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, Self

import httpx2
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.profiles import get_valid_access_token
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.github.http import GitHubClient, GraphQLError, RepoClient

logger = logging.getLogger(__name__)

router = APIRouter(tags=["review"])

_GITHUB_TIMEOUT = httpx2.Timeout(15.0, connect=5.0)
# Hidden markers (Open SWE's finding ids, bot metadata) are not part of what a person wrote.
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

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


class _GitHubReview(BaseModel):
    id: int
    user: _GitHubUser | None = None
    state: str
    submitted_at: datetime | None = None
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


class _GitHubCommitPerson(BaseModel):
    date: datetime


class _GitHubCommitDetail(BaseModel):
    message: str
    author: _GitHubCommitPerson


class _GitHubCommit(BaseModel):
    sha: str
    html_url: str
    commit: _GitHubCommitDetail
    author: _GitHubUser | None = None


class _ThreadRootComment(BaseModel):
    fullDatabaseId: str


class _ThreadRootComments(BaseModel):
    nodes: list[_ThreadRootComment] = []


class _ThreadState(BaseModel):
    id: str
    isResolved: bool
    isOutdated: bool
    comments: _ThreadRootComments = _ThreadRootComments()


class _PageInfo(BaseModel):
    hasNextPage: bool = False
    endCursor: str | None = None


class _ThreadStates(BaseModel):
    pageInfo: _PageInfo = _PageInfo()
    nodes: list[_ThreadState] = []


class _ThreadStatesPullRequest(BaseModel):
    reviewThreads: _ThreadStates = _ThreadStates()


class _ThreadStatesRepository(BaseModel):
    pullRequest: _ThreadStatesPullRequest


class _ThreadStatesData(BaseModel):
    repository: _ThreadStatesRepository


class _GitHubError(BaseModel):
    message: str | None = None
    errors: list[dict[str, object] | str] = Field(default_factory=list)


class ConversationAuthor(BaseModel):
    login: str
    avatar_url: str
    bot: bool


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
    author: ConversationAuthor | None
    created_at: datetime
    body: str
    html_url: str


class ReviewThread(BaseModel):
    """An inline thread: its first comment's anchor, then every reply in order."""

    id: int
    node_id: str | None
    review_id: int | None
    path: str
    line: int | None
    start_line: int | None
    side: DiffSide
    original_line: int | None
    diff_hunk: str
    outdated: bool
    resolved: bool
    comments: list[ThreadComment]


class Conversation(BaseModel):
    items: list[ConversationItem]
    threads: list[ReviewThread]


class ConversationCommentCreate(BaseModel):
    body: str = Field(max_length=65536)


class ThreadResolution(BaseModel):
    resolved: bool


_ISSUE_COMMENTS = TypeAdapter(list[_GitHubIssueComment])
_REVIEWS = TypeAdapter(list[_GitHubReview])
_REVIEW_COMMENTS = TypeAdapter(list[_GitHubReviewComment])
_COMMITS = TypeAdapter(list[_GitHubCommit])
_REVIEW_STATE = TypeAdapter(ReviewState)
_REVIEW_STATES: frozenset[str] = frozenset(
    ("APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED")
)

_THREAD_STATES = """
query($owner: String!, $repo: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { id isResolved isOutdated comments(first: 1) { nodes { fullDatabaseId } } }
      }
    }
  }
}
"""

_RESOLVE_THREAD = """
mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { isResolved } } }
"""

_UNRESOLVE_THREAD = """
mutation($id: ID!) { unresolveReviewThread(input: {threadId: $id}) { thread { isResolved } } }
"""


def _author(user: _GitHubUser | None) -> ConversationAuthor | None:
    if user is None:
        return None
    return ConversationAuthor(login=user.login, avatar_url=user.avatar_url, bot=user.type == "Bot")


def _display_body(body: str | None) -> str:
    return _HTML_COMMENT_RE.sub("", body or "").strip()


def _comment_item(comment: _GitHubIssueComment) -> ConversationComment:
    return ConversationComment(
        id=comment.id,
        author=_author(comment.user),
        created_at=comment.created_at,
        body=_display_body(comment.body),
        html_url=comment.html_url,
    )


def _thread_comment(comment: _GitHubReviewComment) -> ThreadComment:
    return ThreadComment(
        id=comment.id,
        author=_author(comment.user),
        created_at=comment.created_at,
        body=_display_body(comment.body),
        html_url=comment.html_url,
    )


def build_timeline(
    comments: list[_GitHubIssueComment],
    reviews: list[_GitHubReview],
    review_comments: list[_GitHubReviewComment],
    commits: list[_GitHubCommit],
) -> list[ConversationItem]:
    inline_counts = Counter(
        c.pull_request_review_id for c in review_comments if c.pull_request_review_id is not None
    )
    items: list[ConversationItem] = [_comment_item(comment) for comment in comments]
    for review in reviews:
        if review.state not in _REVIEW_STATES or review.submitted_at is None:
            continue
        items.append(
            ConversationReview(
                id=review.id,
                author=_author(review.user),
                created_at=review.submitted_at,
                body=_display_body(review.body),
                html_url=review.html_url,
                state=_REVIEW_STATE.validate_python(review.state),
                inline_comment_count=inline_counts[review.id],
            )
        )
    items.extend(
        ConversationCommit(
            sha=commit.sha,
            author=_author(commit.author),
            created_at=commit.commit.author.date,
            message=commit.commit.message,
            html_url=commit.html_url,
        )
        for commit in commits
    )
    items.sort(key=lambda item: item.created_at)
    return items


def build_threads(
    review_comments: list[_GitHubReviewComment], states: Mapping[int, _ThreadState]
) -> list[ReviewThread]:
    """Group inline comments into threads; a reply points at its thread's first comment."""
    ordered = sorted(review_comments, key=lambda c: c.created_at)
    replies: dict[int, list[_GitHubReviewComment]] = {}
    for comment in ordered:
        if comment.in_reply_to_id is not None:
            replies.setdefault(comment.in_reply_to_id, []).append(comment)
    threads: list[ReviewThread] = []
    for root in ordered:
        if root.in_reply_to_id is not None:
            continue
        state = states.get(root.id)
        threads.append(
            ReviewThread(
                id=root.id,
                node_id=state.id if state else None,
                review_id=root.pull_request_review_id,
                path=root.path,
                line=root.line,
                start_line=root.start_line,
                side=root.side or "RIGHT",
                original_line=root.original_line,
                diff_hunk=root.diff_hunk,
                outdated=state.isOutdated if state else root.line is None,
                resolved=state.isResolved if state else False,
                comments=[_thread_comment(c) for c in [root, *replies.get(root.id, [])]],
            )
        )
    return threads


def _github_error_message(response: httpx2.Response) -> str:
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


@dataclass(frozen=True, slots=True)
class PullRequestConversation:
    """One pull request's conversation, read and written with the viewer's own GitHub token."""

    repo: RepoClient
    number: int

    @classmethod
    @asynccontextmanager
    async def open(cls, login: str, owner: str, repo: str, number: int) -> AsyncIterator[Self]:
        """GitHub failures inside the block become HTTP errors carrying GitHub's message."""
        await require_repo_access_for_user(login, f"{owner}/{repo}")
        token = await get_valid_access_token(login)
        if not token:
            raise HTTPException(401, "GitHub re-auth required")
        try:
            async with GitHubClient.connect(token=token, timeout=_GITHUB_TIMEOUT) as github:
                yield cls(github.repo(owner, repo), number)
        except httpx2.HTTPStatusError as exc:
            message = _github_error_message(exc.response)
            status = exc.response.status_code
            logger.warning(
                "GitHub conversation request failed",
                extra={
                    "http_method": exc.request.method,
                    "github_path": exc.request.url.path,
                    "status_code": status,
                    "github_message": message,
                },
            )
            raise HTTPException(status if status < 500 else 502, message) from exc
        except (ValidationError, GraphQLError) as exc:
            logger.warning(
                "GitHub conversation payload invalid",
                extra={"repo_full_name": f"{owner}/{repo}", "pr_number": number},
                exc_info=exc,
            )
            raise HTTPException(502, "unexpected GitHub response") from exc

    async def _thread_states(self) -> dict[int, _ThreadState]:
        """Each thread's node id and resolution, keyed by its first comment's id."""
        states: dict[int, _ThreadState] = {}
        cursor: str | None = None
        while True:
            data = _ThreadStatesData.model_validate(
                await self.repo.graphql(_THREAD_STATES, {"number": self.number, "after": cursor})
            )
            page = data.repository.pullRequest.reviewThreads
            for thread in page.nodes:
                if thread.comments.nodes:
                    states[int(thread.comments.nodes[0].fullDatabaseId)] = thread
            if not page.pageInfo.hasNextPage or not page.pageInfo.endCursor:
                return states
            cursor = page.pageInfo.endCursor

    async def load(self) -> Conversation:
        raw_comments, raw_reviews, raw_review_comments, raw_commits, states = await asyncio.gather(
            self.repo.pages(f"issues/{self.number}/comments"),
            self.repo.pages(f"pulls/{self.number}/reviews"),
            self.repo.pages(f"pulls/{self.number}/comments"),
            self.repo.pages(f"pulls/{self.number}/commits"),
            self._thread_states(),
        )
        reviews = _REVIEWS.validate_python(raw_reviews)
        pending = {review.id for review in reviews if review.state == "PENDING"}
        # The viewer's unsubmitted comments belong to their pending review, not the conversation.
        review_comments = [
            c
            for c in _REVIEW_COMMENTS.validate_python(raw_review_comments)
            if c.pull_request_review_id not in pending
        ]
        return Conversation(
            items=build_timeline(
                _ISSUE_COMMENTS.validate_python(raw_comments),
                reviews,
                review_comments,
                _COMMITS.validate_python(raw_commits),
            ),
            threads=build_threads(review_comments, states),
        )

    async def comment(self, body: str) -> ConversationComment:
        response = await self.repo.github.request(
            "POST",
            f"repos/{self.repo.full_name}/issues/{self.number}/comments",
            json={"body": body},
        )
        return _comment_item(_GitHubIssueComment.model_validate(response.json()))

    async def reply(self, comment_id: int, body: str) -> ThreadComment:
        response = await self.repo.github.request(
            "POST",
            f"repos/{self.repo.full_name}/pulls/{self.number}/comments/{comment_id}/replies",
            json={"body": body},
        )
        return _thread_comment(_GitHubReviewComment.model_validate(response.json()))

    async def resolve(self, thread_node_id: str, resolved: bool) -> None:
        await self.repo.github.graphql(
            _RESOLVE_THREAD if resolved else _UNRESOLVE_THREAD, {"id": thread_node_id}
        )


def _required_body(body: str) -> str:
    stripped = body.strip()
    if not stripped:
        raise HTTPException(422, "comment body is required")
    return stripped


@router.get("/reviews/{owner}/{repo}/{pr_number}/conversation")
async def api_get_review_conversation(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION_DEP,
) -> Conversation:
    async with PullRequestConversation.open(session["sub"], owner, repo, pr_number) as pull:
        return await pull.load()


@router.post("/reviews/{owner}/{repo}/{pr_number}/conversation/comments")
async def api_post_review_conversation_comment(
    owner: str,
    repo: str,
    pr_number: int,
    comment: ConversationCommentCreate,
    session: dict[str, Any] = SESSION_DEP,
) -> ConversationComment:
    body = _required_body(comment.body)
    async with PullRequestConversation.open(session["sub"], owner, repo, pr_number) as pull:
        return await pull.comment(body)


@router.post("/reviews/{owner}/{repo}/{pr_number}/threads/{comment_id}/replies")
async def api_reply_to_review_thread(
    owner: str,
    repo: str,
    pr_number: int,
    comment_id: int,
    comment: ConversationCommentCreate,
    session: dict[str, Any] = SESSION_DEP,
) -> ThreadComment:
    body = _required_body(comment.body)
    async with PullRequestConversation.open(session["sub"], owner, repo, pr_number) as pull:
        return await pull.reply(comment_id, body)


@router.put(
    "/reviews/{owner}/{repo}/{pr_number}/threads/{thread_node_id}/resolution", status_code=204
)
async def api_set_review_thread_resolution(
    owner: str,
    repo: str,
    pr_number: int,
    thread_node_id: str,
    resolution: ThreadResolution,
    session: dict[str, Any] = SESSION_DEP,
) -> None:
    async with PullRequestConversation.open(session["sub"], owner, repo, pr_number) as pull:
        await pull.resolve(thread_node_id, resolution.resolved)
