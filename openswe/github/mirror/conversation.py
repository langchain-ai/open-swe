"""A pull request's conversation as GitHub describes it: reviews, comments, threads.

Also the commits and changed files of its head, which only the API lists.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import ClassVar, Literal, Self
from uuid import UUID

from pydantic import AliasPath, BaseModel, Field, field_validator
from sqlalchemy import BigInteger, ColumnElement, ForeignKey, Text, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.github.mirror.rows import Excluded, MirrorRow

ReviewState = Literal["approved", "changes_requested", "commented", "dismissed"]


class _Author(BaseModel):
    login: str = ""
    id: int | None = None
    type: str = ""


class Review(MirrorRow):
    """A submitted review; pending ones are only visible to their author and never stored."""

    __tablename__ = "github_review"
    natural_key: ClassVar[tuple[str, ...]] = ("github_id",)

    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey("pull_request.id", ondelete="CASCADE"))
    github_id: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[ReviewState] = mapped_column(Text)
    author_login: Mapped[str] = mapped_column(default="")
    author_github_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    body: Mapped[str] = mapped_column(default="")
    html_url: Mapped[str] = mapped_column(default="")
    commit_sha: Mapped[str] = mapped_column(default="")
    submitted_at: Mapped[datetime | None] = mapped_column(default=None)

    @classmethod
    async def store(cls, session: AsyncSession, rows: Sequence[Self]) -> None:
        # Dismissal is final, whatever order the deliveries arrive in.
        await cls.put(
            session,
            rows,
            unless_newer=lambda new: or_(cls.state != "dismissed", new.state == "dismissed"),
        )


class ReviewComment(MirrorRow):
    """An inline comment; ``in_reply_to_github_id`` names its thread's first comment."""

    __tablename__ = "github_review_comment"
    natural_key: ClassVar[tuple[str, ...]] = ("github_id",)

    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey("pull_request.id", ondelete="CASCADE"))
    github_id: Mapped[int] = mapped_column(BigInteger)
    review_github_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    in_reply_to_github_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    author_login: Mapped[str] = mapped_column(default="")
    author_github_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    author_type: Mapped[str] = mapped_column(default="")
    body: Mapped[str] = mapped_column(default="")
    html_url: Mapped[str] = mapped_column(default="")
    path: Mapped[str] = mapped_column(default="")
    diff_hunk: Mapped[str] = mapped_column(default="")
    line: Mapped[int | None] = mapped_column(default=None)
    original_line: Mapped[int | None] = mapped_column(default=None)
    start_line: Mapped[int | None] = mapped_column(default=None)
    side: Mapped[str] = mapped_column(default="")
    commit_sha: Mapped[str] = mapped_column(default="")
    github_created_at: Mapped[datetime | None] = mapped_column(default=None)
    github_updated_at: Mapped[datetime | None] = mapped_column(default=None)

    @property
    def thread_root(self) -> int:
        return self.in_reply_to_github_id or self.github_id

    @classmethod
    async def store(cls, session: AsyncSession, rows: Sequence[Self]) -> None:
        await cls.put(session, rows, unless_newer=lambda new: _edited_since(cls, new))
        await ReviewThread.add_missing(
            session,
            [
                ReviewThread(
                    pull_request_id=row.pull_request_id,
                    root_comment_github_id=row.github_id,
                    # A comment the diff no longer has a line for.
                    outdated=row.line is None,
                )
                for row in rows
                if row.in_reply_to_github_id is None
            ],
        )


class ReviewThread(MirrorRow):
    """An inline thread, named by the comment that opened it."""

    __tablename__ = "github_review_thread"
    natural_key: ClassVar[tuple[str, ...]] = ("root_comment_github_id",)

    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey("pull_request.id", ondelete="CASCADE"))
    root_comment_github_id: Mapped[int] = mapped_column(BigInteger)
    # GraphQL's id, which resolving needs; ``""`` until a thread delivery or a sync names it.
    node_id: Mapped[str] = mapped_column(default="")
    resolved: Mapped[bool] = mapped_column(default=False)
    outdated: Mapped[bool] = mapped_column(default=False)


class IssueComment(MirrorRow):
    """A comment on the pull request's conversation tab."""

    __tablename__ = "github_issue_comment"
    natural_key: ClassVar[tuple[str, ...]] = ("github_id",)

    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey("pull_request.id", ondelete="CASCADE"))
    github_id: Mapped[int] = mapped_column(BigInteger)
    author_login: Mapped[str] = mapped_column(default="")
    author_github_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    author_type: Mapped[str] = mapped_column(default="")
    body: Mapped[str] = mapped_column(default="")
    html_url: Mapped[str] = mapped_column(default="")
    github_created_at: Mapped[datetime | None] = mapped_column(default=None)
    github_updated_at: Mapped[datetime | None] = mapped_column(default=None)

    @classmethod
    async def store(cls, session: AsyncSession, rows: Sequence[Self]) -> None:
        await cls.put(session, rows, unless_newer=lambda new: _edited_since(cls, new))


class PullRequestCommit(MirrorRow):
    """A commit on the pull request's head, at ``position`` in GitHub's listing."""

    __tablename__ = "github_pull_request_commit"
    natural_key: ClassVar[tuple[str, ...]] = ("pull_request_id", "sha")

    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey("pull_request.id", ondelete="CASCADE"))
    sha: Mapped[str]
    position: Mapped[int]
    message: Mapped[str] = mapped_column(default="")
    author_login: Mapped[str] = mapped_column(default="")
    author_github_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    author_type: Mapped[str] = mapped_column(default="")
    authored_at: Mapped[datetime | None] = mapped_column(default=None)
    html_url: Mapped[str] = mapped_column(default="")


class PullRequestFile(MirrorRow):
    """A file the pull request changes, as of ``pull_request.synced_head_sha``."""

    __tablename__ = "github_pull_request_file"
    natural_key: ClassVar[tuple[str, ...]] = ("pull_request_id", "path")

    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey("pull_request.id", ondelete="CASCADE"))
    path: Mapped[str]
    previous_path: Mapped[str] = mapped_column(default="")
    status: Mapped[str] = mapped_column(default="")
    additions: Mapped[int] = mapped_column(default=0)
    deletions: Mapped[int] = mapped_column(default=0)


def _edited_since(stored: type[IssueComment | ReviewComment], new: Excluded) -> ColumnElement[bool]:
    return or_(
        stored.github_updated_at.is_(None),
        new.github_updated_at >= stored.github_updated_at,
    )


async def replace_listing[R: PullRequestCommit | PullRequestFile](
    session: AsyncSession, model: type[R], pull_request_id: UUID, rows: Sequence[R]
) -> None:
    """Make ``rows`` the whole of the pull request's commits or files."""
    await session.execute(delete(model).where(model.pull_request_id == pull_request_id))
    await model.put(session, rows)


async def drop_unlisted(
    session: AsyncSession,
    model: type[Review | ReviewComment | IssueComment],
    pull_request_id: UUID,
    listed: Sequence[int],
    *,
    read_at: datetime,
) -> None:
    """Delete stored records GitHub no longer lists, unless written since the listing was read."""
    await session.execute(
        delete(model).where(
            model.pull_request_id == pull_request_id,
            model.github_id.not_in(listed),
            model.updated_at < read_at,
        )
    )


async def delete_by_github_id(
    session: AsyncSession, model: type[ReviewComment | IssueComment], github_id: int
) -> None:
    await session.execute(delete(model).where(model.github_id == github_id))
    if model is ReviewComment:
        await session.execute(
            delete(ReviewThread).where(ReviewThread.root_comment_github_id == github_id)
        )


# GitHub's descriptions of these records, as REST and webhooks send them.


class ReviewPayload(BaseModel):
    id: int
    state: ReviewState | Literal["pending"]
    user: _Author | None = None
    body: str | None = None
    html_url: str = ""
    commit_id: str | None = None
    submitted_at: datetime | None = None

    @field_validator("state", mode="before")
    @classmethod
    def _lowercase(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value

    def to_row(self, pull_request_id: UUID) -> Review | None:
        """``None`` for a pending review, which nobody but its author may see."""
        if self.state == "pending":
            return None
        author = self.user or _Author()
        return Review(
            pull_request_id=pull_request_id,
            github_id=self.id,
            state=self.state,
            author_login=author.login,
            author_github_id=author.id,
            body=self.body or "",
            html_url=self.html_url,
            commit_sha=self.commit_id or "",
            submitted_at=self.submitted_at,
        )


class ReviewCommentPayload(BaseModel):
    id: int
    pull_request_review_id: int | None = None
    in_reply_to_id: int | None = None
    user: _Author | None = None
    body: str | None = None
    html_url: str = ""
    path: str = ""
    diff_hunk: str | None = None
    line: int | None = None
    original_line: int | None = None
    start_line: int | None = None
    side: str | None = None
    commit_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_row(self, pull_request_id: UUID) -> ReviewComment:
        author = self.user or _Author()
        return ReviewComment(
            pull_request_id=pull_request_id,
            github_id=self.id,
            review_github_id=self.pull_request_review_id,
            in_reply_to_github_id=self.in_reply_to_id,
            author_login=author.login,
            author_github_id=author.id,
            author_type=author.type,
            body=self.body or "",
            html_url=self.html_url,
            path=self.path,
            diff_hunk=self.diff_hunk or "",
            line=self.line,
            original_line=self.original_line,
            start_line=self.start_line,
            side=self.side or "",
            commit_sha=self.commit_id or "",
            github_created_at=self.created_at,
            github_updated_at=self.updated_at,
        )


class IssueCommentPayload(BaseModel):
    id: int
    user: _Author | None = None
    body: str | None = None
    html_url: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_row(self, pull_request_id: UUID) -> IssueComment:
        author = self.user or _Author()
        return IssueComment(
            pull_request_id=pull_request_id,
            github_id=self.id,
            author_login=author.login,
            author_github_id=author.id,
            author_type=author.type,
            body=self.body or "",
            html_url=self.html_url,
            github_created_at=self.created_at,
            github_updated_at=self.updated_at,
        )


class CommitPayload(BaseModel):
    sha: str
    html_url: str = ""
    message: str = Field("", validation_alias=AliasPath("commit", "message"))
    authored_at: datetime | None = Field(
        None, validation_alias=AliasPath("commit", "author", "date")
    )
    author: _Author | None = None

    def to_row(self, pull_request_id: UUID, position: int) -> PullRequestCommit:
        author = self.author or _Author()
        return PullRequestCommit(
            pull_request_id=pull_request_id,
            sha=self.sha,
            position=position,
            message=self.message,
            author_login=author.login,
            author_github_id=author.id,
            author_type=author.type,
            authored_at=self.authored_at,
            html_url=self.html_url,
        )


class FilePayload(BaseModel):
    filename: str
    previous_filename: str | None = None
    status: str = ""
    additions: int = 0
    deletions: int = 0

    def to_row(self, pull_request_id: UUID) -> PullRequestFile:
        return PullRequestFile(
            pull_request_id=pull_request_id,
            path=self.filename,
            previous_path=self.previous_filename or "",
            status=self.status,
            additions=self.additions,
            deletions=self.deletions,
        )


class _ThreadComment(BaseModel):
    full_database_id: int | None = Field(None, alias="fullDatabaseId")


class _ThreadComments(BaseModel):
    nodes: list[_ThreadComment] = Field(default_factory=list)


class ThreadNode(BaseModel):
    """A review thread as GraphQL describes it."""

    id: str
    is_resolved: bool = Field(False, alias="isResolved")
    is_outdated: bool = Field(False, alias="isOutdated")
    comments: _ThreadComments = Field(default_factory=_ThreadComments)

    def to_row(self, pull_request_id: UUID) -> ReviewThread | None:
        root = next((c.full_database_id for c in self.comments.nodes if c.full_database_id), None)
        if root is None:
            return None
        return ReviewThread(
            pull_request_id=pull_request_id,
            root_comment_github_id=root,
            node_id=self.id,
            resolved=self.is_resolved,
            outdated=self.is_outdated,
        )


class ThreadEventPayload(BaseModel):
    """The ``thread`` of a ``pull_request_review_thread`` delivery."""

    node_id: str
    comments: list[ReviewCommentPayload] = Field(default_factory=list)

    def to_row(self, pull_request_id: UUID, *, resolved: bool) -> ReviewThread | None:
        roots = [comment.id for comment in self.comments if comment.in_reply_to_id is None]
        if not roots:
            return None
        return ReviewThread(
            pull_request_id=pull_request_id,
            root_comment_github_id=roots[0],
            node_id=self.node_id,
            resolved=resolved,
        )
