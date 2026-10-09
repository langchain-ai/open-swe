"""A pull request's conversation on GitHub: timeline, inline threads, and the viewer's replies."""

import asyncio
import logging
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.github.http import GitHubClient
from openswe.github.pull_request_status import PullRequestClient

logger = logging.getLogger(__name__)

router = APIRouter(tags=["review"])

# Hidden markers (Open SWE's finding ids, bot metadata) are not part of what a person wrote.
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# Open SWE stamps everything it publishes, including what it posts with a person's token.
_OPEN_SWE_MARKER_RE = re.compile(r"<!--\s*open-swe-review(?:er|-comment)\b")
OPEN_SWE_LOGIN = "open-swe"
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


class _ThreadStatesPage(BaseModel):
    pageInfo: _PageInfo = _PageInfo()
    nodes: list[_ThreadState] = []


class _ThreadStatesPullRequest(BaseModel):
    reviewThreads: _ThreadStatesPage = _ThreadStatesPage()


class _ThreadStatesRepository(BaseModel):
    pullRequest: _ThreadStatesPullRequest


class _ThreadStatesData(BaseModel):
    repository: _ThreadStatesRepository


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
    # Set when Open SWE posted through this person's GitHub account; ``login`` is then Open SWE.
    posted_by: str | None = None

    @classmethod
    def of(cls, user: _GitHubUser | None, body: str | None = None) -> Self | None:
        if user is None:
            return None
        bot = user.type == "Bot"
        if not bot and body and _OPEN_SWE_MARKER_RE.search(body):
            return cls(login=OPEN_SWE_LOGIN, avatar_url="", bot=True, posted_by=user.login)
        return cls(login=user.login, avatar_url=user.avatar_url, bot=bot)


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
            author=ConversationAuthor.of(comment.user, comment.body),
            created_at=comment.created_at,
            body=_display_body(comment.body),
            html_url=comment.html_url,
        )

    @classmethod
    async def post(cls, pull: PullRequestClient, body: str) -> Self:
        created = await pull.comment(body)
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
            author=ConversationAuthor.of(comment.user, comment.body),
            created_at=comment.created_at,
            body=_display_body(comment.body),
            html_url=comment.html_url,
        )

    @classmethod
    async def reply(cls, pull: PullRequestClient, comment_id: int, body: str) -> Self:
        created = await pull.reply_to_review_comment(comment_id, body)
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
        cls, review_comments: list[_GitHubReviewComment], states: Mapping[int, _ThreadState]
    ) -> list[Self]:
        """Group inline comments into threads; a reply points at its thread's first comment."""
        ordered = sorted(review_comments, key=lambda c: c.created_at)
        replies: dict[int, list[_GitHubReviewComment]] = {}
        for comment in ordered:
            if comment.in_reply_to_id is not None:
                replies.setdefault(comment.in_reply_to_id, []).append(comment)
        threads: list[Self] = []
        for root in ordered:
            if root.in_reply_to_id is not None:
                continue
            state = states.get(root.id)
            threads.append(
                cls(
                    id=root.id,
                    node_id=state.id if state else None,
                    path=root.path,
                    line=root.line,
                    start_line=root.start_line,
                    side=root.side or "RIGHT",
                    original_line=root.original_line,
                    diff_hunk=root.diff_hunk,
                    outdated=state.isOutdated if state else root.line is None,
                    resolved=state.isResolved if state else False,
                    comments=[ThreadComment.of(c) for c in [root, *replies.get(root.id, [])]],
                )
            )
        return threads

    @staticmethod
    async def states(pull: PullRequestClient) -> dict[int, _ThreadState]:
        """Each thread's node id and resolution, keyed by its first comment's id."""
        states: dict[int, _ThreadState] = {}
        cursor: str | None = None
        while True:
            data = _ThreadStatesData.model_validate(
                await pull.repo.graphql(_THREAD_STATES, {"number": pull.number, "after": cursor})
            )
            page = data.repository.pullRequest.reviewThreads
            for thread in page.nodes:
                if thread.comments.nodes:
                    states[int(thread.comments.nodes[0].fullDatabaseId)] = thread
            if not page.pageInfo.hasNextPage or not page.pageInfo.endCursor:
                return states
            cursor = page.pageInfo.endCursor

    @staticmethod
    async def set_resolved(pull: PullRequestClient, node_id: str, resolved: bool) -> None:
        await pull.repo.github.graphql(
            _RESOLVE_THREAD if resolved else _UNRESOLVE_THREAD, {"id": node_id}
        )


_ISSUE_COMMENTS = TypeAdapter(list[_GitHubIssueComment])
_REVIEWS = TypeAdapter(list[_GitHubReview])
_REVIEW_COMMENTS = TypeAdapter(list[_GitHubReviewComment])
_COMMITS = TypeAdapter(list[_GitHubCommit])
_REVIEW_STATE = TypeAdapter(ReviewState)
_REVIEW_STATES: frozenset[str] = frozenset(
    ("APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED")
)


class Conversation(BaseModel):
    items: list[ConversationItem]
    threads: list[ReviewThread]

    @classmethod
    def of(
        cls,
        comments: list[_GitHubIssueComment],
        reviews: list[_GitHubReview],
        review_comments: list[_GitHubReviewComment],
        commits: list[_GitHubCommit],
        states: Mapping[int, _ThreadState],
    ) -> Self:
        items: list[ConversationItem] = [ConversationComment.of(comment) for comment in comments]
        for review in reviews:
            if review.state not in _REVIEW_STATES or review.submitted_at is None:
                continue
            items.append(
                ConversationReview(
                    id=review.id,
                    author=ConversationAuthor.of(review.user, review.body),
                    created_at=review.submitted_at,
                    body=_display_body(review.body),
                    html_url=review.html_url,
                    state=_REVIEW_STATE.validate_python(review.state),
                )
            )
        items.extend(
            ConversationCommit(
                sha=commit.sha,
                author=ConversationAuthor.of(commit.author),
                created_at=commit.commit.author.date,
                message=commit.commit.message,
                html_url=commit.html_url,
            )
            for commit in commits
        )
        items.sort(key=lambda item: item.created_at)
        return cls(items=items, threads=ReviewThread.group(review_comments, states))

    @classmethod
    async def load(cls, pull: PullRequestClient) -> Self:
        with _expected_shape(pull):
            (
                raw_comments,
                raw_reviews,
                raw_review_comments,
                raw_commits,
                states,
            ) = await asyncio.gather(
                pull.issue_comments(),
                pull.reviews(),
                pull.review_comments(),
                pull.commits(),
                ReviewThread.states(pull),
            )
            reviews = _REVIEWS.validate_python(raw_reviews)
            pending = {review.id for review in reviews if review.state == "PENDING"}
            # The viewer's unsubmitted comments belong to their pending review, not the conversation.
            review_comments = [
                c
                for c in _REVIEW_COMMENTS.validate_python(raw_review_comments)
                if c.pull_request_review_id not in pending
            ]
            return cls.of(
                _ISSUE_COMMENTS.validate_python(raw_comments),
                reviews,
                review_comments,
                _COMMITS.validate_python(raw_commits),
                states,
            )


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
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    async with GitHubClient.as_user(session["sub"]) as github:
        return await Conversation.load(github.repo(owner, repo).pull_request(pr_number))


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
        await ReviewThread.set_resolved(
            github.repo(owner, repo).pull_request(pr_number),
            thread_node_id,
            resolution.resolved,
        )
