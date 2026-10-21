"""CI as GitHub reports it on commits: check runs, commit statuses, Actions runs.

These belong to a commit, not a pull request, so they are keyed by repository
and SHA; a pull request finds its CI through its ``head_sha``.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Literal, Self
from uuid import UUID

from pydantic import AliasPath, BaseModel, Field
from sqlalchemy import BigInteger, ForeignKey, Text, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.github.mirror.rows import MirrorRow

CheckState = Literal["passing", "failing", "pending", "unknown", "none"]
StatusState = Literal["error", "failure", "pending", "success"]

FAILING_CONCLUSIONS = frozenset({"failure", "timed_out", "action_required", "startup_failure"})
# Finished without saying whether the code is good.
INCONCLUSIVE_CONCLUSIONS = frozenset({"cancelled", "stale", "skipped", "neutral"})
_INTERRUPTED = frozenset({"cancelled", "stale"})
_ACTIONS_SLUG = "github-actions"


class CheckRun(MirrorRow):
    __tablename__ = "github_check_run"
    natural_key: ClassVar[tuple[str, ...]] = ("github_id",)

    repository_id: Mapped[UUID] = mapped_column(ForeignKey("repository.id", ondelete="CASCADE"))
    github_id: Mapped[int] = mapped_column(BigInteger)
    head_sha: Mapped[str]
    name: Mapped[str]
    status: Mapped[str]
    conclusion: Mapped[str] = mapped_column(default="")
    details_url: Mapped[str] = mapped_column(default="")
    html_url: Mapped[str] = mapped_column(default="")
    app_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    app_slug: Mapped[str] = mapped_column(default="")
    check_suite_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    completed_at: Mapped[datetime | None] = mapped_column(default=None)

    @property
    def completed(self) -> bool:
        return self.status == "completed"

    @property
    def failing(self) -> bool:
        return self.completed and self.conclusion in FAILING_CONCLUSIONS

    @property
    def url(self) -> str:
        return self.details_url or self.html_url

    @classmethod
    async def store(cls, session: AsyncSession, rows: Sequence[Self]) -> None:
        # A run never goes back from completed; a rerun is a new run.
        await cls.put(
            session,
            rows,
            unless_newer=lambda new: or_(cls.status != "completed", new.status == "completed"),
        )


class CommitStatus(MirrorRow):
    """The latest status a ``context`` reported on a commit."""

    __tablename__ = "github_commit_status"
    natural_key: ClassVar[tuple[str, ...]] = ("repository_id", "sha", "context")

    repository_id: Mapped[UUID] = mapped_column(ForeignKey("repository.id", ondelete="CASCADE"))
    sha: Mapped[str]
    context: Mapped[str]
    state: Mapped[StatusState] = mapped_column(Text)
    description: Mapped[str] = mapped_column(default="")
    target_url: Mapped[str] = mapped_column(default="")
    github_updated_at: Mapped[datetime | None] = mapped_column(default=None)

    @property
    def failing(self) -> bool:
        return self.state in {"failure", "error"}

    @classmethod
    async def store(cls, session: AsyncSession, rows: Sequence[Self]) -> None:
        await cls.put(
            session,
            rows,
            unless_newer=lambda new: or_(
                cls.github_updated_at.is_(None), new.github_updated_at >= cls.github_updated_at
            ),
        )


class WorkflowRun(MirrorRow):
    """An Actions run, kept to tell which cancelled check runs a newer run superseded."""

    __tablename__ = "github_workflow_run"
    natural_key: ClassVar[tuple[str, ...]] = ("github_id",)

    repository_id: Mapped[UUID] = mapped_column(ForeignKey("repository.id", ondelete="CASCADE"))
    github_id: Mapped[int] = mapped_column(BigInteger)
    head_sha: Mapped[str]
    workflow_id: Mapped[int] = mapped_column(BigInteger)
    check_suite_id: Mapped[int] = mapped_column(BigInteger)
    event: Mapped[str] = mapped_column(default="")


@dataclass(frozen=True, slots=True)
class FailingCheck:
    name: str
    conclusion: str
    url: str


@dataclass(frozen=True, slots=True)
class HeadChecks:
    """The CI that counts on one commit.

    ``runs`` keeps the latest run of each check per suite, as GitHub's
    ``filter=latest`` does, and drops Actions runs cancelled because a newer run
    of the same workflow replaced them.
    """

    runs: tuple[CheckRun, ...]
    statuses: tuple[CommitStatus, ...]

    @classmethod
    async def for_heads(
        cls, session: AsyncSession, heads: Iterable[tuple[UUID, str]]
    ) -> dict[tuple[UUID, str], Self]:
        """Each ``(repository_id, sha)``'s checks, an empty one where none reported."""
        keys = list({head for head in heads if head[1]})
        found: dict[tuple[UUID, str], Self] = {key: cls((), ()) for key in keys}
        if not keys:
            return found
        runs = await session.scalars(
            select(CheckRun)
            .where(tuple_(CheckRun.repository_id, CheckRun.head_sha).in_(keys))
            .order_by(CheckRun.github_id)
        )
        statuses = await session.scalars(
            select(CommitStatus)
            .where(tuple_(CommitStatus.repository_id, CommitStatus.sha).in_(keys))
            .order_by(CommitStatus.context)
        )
        workflows = await session.scalars(
            select(WorkflowRun).where(
                tuple_(WorkflowRun.repository_id, WorkflowRun.head_sha).in_(keys)
            )
        )
        by_suite = {workflow.check_suite_id: workflow for workflow in workflows}
        runs_by_head: dict[tuple[UUID, str], list[CheckRun]] = {}
        for run in runs:
            runs_by_head.setdefault((run.repository_id, run.head_sha), []).append(run)
        statuses_by_head: dict[tuple[UUID, str], list[CommitStatus]] = {}
        for status in statuses:
            statuses_by_head.setdefault((status.repository_id, status.sha), []).append(status)
        for key in keys:
            found[key] = cls(
                tuple(_current(runs_by_head.get(key, []), by_suite)),
                tuple(statuses_by_head.get(key, [])),
            )
        return found

    @property
    def reported(self) -> bool:
        return bool(self.runs or self.statuses)

    @property
    def failing(self) -> list[FailingCheck]:
        return [
            FailingCheck(run.name, run.conclusion, run.url) for run in self.runs if run.failing
        ] + [
            FailingCheck(status.context, status.state, status.target_url)
            for status in self.statuses
            if status.failing
        ]

    @property
    def pending(self) -> list[str]:
        return [run.name for run in self.runs if not run.completed] + [
            status.context for status in self.statuses if status.state == "pending"
        ]

    @property
    def inconclusive(self) -> int:
        return sum(
            1 for run in self.runs if run.completed and run.conclusion in INCONCLUSIVE_CONCLUSIONS
        )

    @property
    def state(self) -> CheckState:
        if self.failing:
            return "failing"
        if self.pending:
            return "pending"
        if any(run.completed and run.conclusion in _INTERRUPTED for run in self.runs):
            return "unknown"
        return "passing" if self.reported else "none"

    def unreported(self, required: Iterable[tuple[str, int | None]]) -> list[str]:
        """Required ``(name, app_id)`` checks nothing has reported on this commit yet."""
        missing: set[str] = set()
        for name, app_id in required:
            by_run = any(
                run.name == name and (app_id is None or run.app_id == app_id) for run in self.runs
            )
            by_status = app_id is None and any(status.context == name for status in self.statuses)
            if not by_run and not by_status:
                missing.add(name)
        return sorted(missing)


def _current(runs: Sequence[CheckRun], workflows: dict[int, WorkflowRun]) -> list[CheckRun]:
    latest: dict[tuple[int | None, str], CheckRun] = {}
    for run in runs:
        latest[(run.check_suite_id, run.name)] = run
    current = list(latest.values())
    newest: dict[tuple[int | None, int, str, str], int] = {}
    for run in current:
        identity = _workflow_identity(run, workflows)
        if identity is not None:
            workflow = workflows[run.check_suite_id or 0]
            newest[identity] = max(newest.get(identity, workflow.github_id), workflow.github_id)
    return [
        run
        for run in current
        if run.conclusion not in _INTERRUPTED
        or (identity := _workflow_identity(run, workflows)) is None
        or workflows[run.check_suite_id or 0].github_id == newest[identity]
    ]


def _workflow_identity(
    run: CheckRun, workflows: dict[int, WorkflowRun]
) -> tuple[int | None, int, str, str] | None:
    workflow = workflows.get(run.check_suite_id or 0)
    if run.app_slug != _ACTIONS_SLUG or workflow is None:
        return None
    return (run.app_id, workflow.workflow_id, workflow.event, run.name)


# GitHub's descriptions of these records, as REST and webhooks send them.


class CheckRunPayload(BaseModel):
    id: int
    head_sha: str
    name: str
    status: str
    conclusion: str | None = None
    details_url: str | None = None
    html_url: str | None = None
    app_id: int | None = Field(None, validation_alias=AliasPath("app", "id"))
    app_slug: str | None = Field(None, validation_alias=AliasPath("app", "slug"))
    check_suite_id: int | None = Field(None, validation_alias=AliasPath("check_suite", "id"))
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def to_row(self, repository_id: UUID) -> CheckRun:
        return CheckRun(
            repository_id=repository_id,
            github_id=self.id,
            head_sha=self.head_sha,
            name=self.name,
            status=self.status,
            conclusion=self.conclusion or "",
            details_url=self.details_url or "",
            html_url=self.html_url or "",
            app_id=self.app_id,
            app_slug=self.app_slug or "",
            check_suite_id=self.check_suite_id,
            started_at=self.started_at,
            completed_at=self.completed_at,
        )


class StatusPayload(BaseModel):
    """One status; a ``status`` delivery names its ``sha``, a combined-status listing does not."""

    context: str
    state: StatusState
    sha: str = ""
    description: str | None = None
    target_url: str | None = None
    updated_at: datetime | None = None

    def to_row(self, repository_id: UUID, sha: str = "") -> CommitStatus:
        return CommitStatus(
            repository_id=repository_id,
            sha=sha or self.sha,
            context=self.context,
            state=self.state,
            description=self.description or "",
            target_url=self.target_url or "",
            github_updated_at=self.updated_at,
        )


class WorkflowRunPayload(BaseModel):
    id: int
    head_sha: str
    workflow_id: int
    check_suite_id: int
    event: str = ""

    def to_row(self, repository_id: UUID) -> WorkflowRun:
        return WorkflowRun(
            repository_id=repository_id,
            github_id=self.id,
            head_sha=self.head_sha,
            workflow_id=self.workflow_id,
            check_suite_id=self.check_suite_id,
            event=self.event,
        )
