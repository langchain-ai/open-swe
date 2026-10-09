"""A pull request's stored walkthrough plan, shared by every surface that walks a reader through it.

Planners add to it one chunk at a time, each under a row lock against the head
the plan is at. Whoever first reads the PR at a new head carries the plan
there. The review page and the reviewer only read a complete plan: one that
places every changed line at the PR's current head.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import ForeignKey, delete, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.pull_requests import PullRequest
from openswe.github.repositories import Repository
from openswe.utils.json_types import JsonObject
from openswe.walkthrough.diff import FileChange
from openswe.walkthrough.plan import LineRange, LineRef, Plan

logger = logging.getLogger(__name__)

OTHER_TITLE = "Other changes"


class PlanMovedError(RuntimeError):
    """The stored plan is no longer at the head the caller planned against."""


class Walkthrough(Base):
    __tablename__ = "pull_request_walkthrough"

    pull_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("pull_request.id", ondelete="CASCADE"), primary_key=True
    )
    head_sha: Mapped[str]
    merge_base_sha: Mapped[str]
    plan_json: Mapped[JsonObject] = mapped_column("plan", JSONB)
    # Every changed line at ``head_sha`` is in a chunk or in Other.
    complete: Mapped[bool] = mapped_column(server_default="false", default=False)
    human_input_summary: Mapped[str] = mapped_column(server_default="", default="")
    # When the plan last became complete.
    generated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    @property
    def plan(self) -> Plan:
        return Plan.model_validate(self.plan_json)

    @classmethod
    async def get(cls, pull_request_id: UUID) -> Self | None:
        async with postgres.session() as session:
            return await session.get(cls, pull_request_id)

    @classmethod
    async def for_pull_request(cls, owner: str, repo: str, number: int) -> Self | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(cls)
                .join(PullRequest, PullRequest.id == cls.pull_request_id)
                .join(PullRequest.repository)
                .where(Repository.key == f"{owner}/{repo}".lower(), PullRequest.number == number)
            )

    @classmethod
    async def install(
        cls,
        pull_request_id: UUID,
        plan: Plan,
        *,
        merge_base_sha: str,
        changes: list[FileChange],
        replacing: str | None,
    ) -> bool:
        """Store ``plan`` if the stored one is still at ``replacing`` (``None``: there is none).

        ``False`` when another reader of the PR moved it first.
        """
        complete = not plan.unplanned(changes)
        values = {
            "head_sha": plan.head_sha,
            "merge_base_sha": merge_base_sha,
            "plan_json": plan.model_dump(mode="json"),
            "complete": complete,
        }
        async with postgres.session() as session:
            if replacing is None:
                stored = await session.scalar(
                    insert(cls)
                    .values(pull_request_id=pull_request_id, **values)
                    .on_conflict_do_nothing()
                    .returning(cls.pull_request_id)
                )
            else:
                stored = await session.scalar(
                    update(cls)
                    .where(cls.pull_request_id == pull_request_id, cls.head_sha == replacing)
                    .values(
                        **values,
                        generated_at=func.clock_timestamp() if complete else cls.generated_at,
                    )
                    .returning(cls.pull_request_id)
                )
            await session.commit()
        return stored is not None

    @classmethod
    async def edit[T](
        cls,
        pull_request_id: UUID,
        *,
        head_sha: str,
        changes: list[FileChange],
        change: Callable[[Plan], T],
    ) -> T:
        """Apply ``change`` to the stored plan under a row lock; it must still be at ``head_sha``."""
        async with postgres.session() as session:
            row = await session.scalar(
                select(cls).where(cls.pull_request_id == pull_request_id).with_for_update()
            )
            if row is None or row.head_sha != head_sha:
                raise PlanMovedError("the pull request moved on; start from its new head")
            plan = row.plan
            result = change(plan)
            complete = not plan.unplanned(changes)
            if complete and not row.complete:
                row.generated_at = func.clock_timestamp()
            row.plan_json = plan.model_dump(mode="json")
            row.complete = complete
            await session.commit()
        return result

    @classmethod
    async def for_head(cls, owner: str, repo: str, number: int, head_sha: str) -> Self | None:
        """The complete plan for ``head_sha``, or ``None`` when there is none yet."""
        walkthrough = await cls.for_pull_request(owner, repo, number)
        if walkthrough is None or walkthrough.head_sha != head_sha or not walkthrough.complete:
            return None
        return walkthrough

    @classmethod
    async def generated_since(cls, owner: str, repo: str, number: int, since: datetime) -> bool:
        """Whether the PR's plan, for any head, became complete at or after ``since``."""
        if not postgres.configured():
            return False
        async with postgres.session() as session:
            found = await session.scalar(
                select(cls.pull_request_id)
                .join(PullRequest, PullRequest.id == cls.pull_request_id)
                .join(PullRequest.repository)
                .where(
                    Repository.key == f"{owner}/{repo}".lower(),
                    PullRequest.number == number,
                    cls.complete,
                    cls.generated_at >= since,
                )
            )
        return found is not None

    @classmethod
    async def dismiss(cls, owner: str, repo: str, number: int) -> bool:
        """Delete the PR's plan so it is planned again; ``False`` when none existed."""
        pull_request = await PullRequest.get(owner, repo, number)
        if pull_request is None:
            return False
        async with postgres.session() as session:
            deleted = await session.scalar(
                delete(cls)
                .where(cls.pull_request_id == pull_request.id)
                .returning(cls.pull_request_id)
            )
            await session.commit()
        return deleted is not None

    @classmethod
    async def human_input_for(cls, pull_request_id: UUID) -> str | None:
        """The PR's human input summary, or ``None`` when it has no plan yet."""
        async with postgres.session() as session:
            return await session.scalar(
                select(cls.human_input_summary).where(cls.pull_request_id == pull_request_id)
            )

    @classmethod
    async def set_human_input(cls, pull_request_id: UUID, summary: str) -> bool:
        """Replace the PR's human input summary; ``False`` when it has no plan yet."""
        async with postgres.session() as session:
            updated = await session.scalar(
                update(cls)
                .where(cls.pull_request_id == pull_request_id)
                .values(human_input_summary=summary)
                .returning(cls.pull_request_id)
            )
            await session.commit()
        return updated is not None

    @classmethod
    async def carry_forward(
        cls, owner: str, repo: str, number: int, *, from_sha: str, to_sha: str
    ) -> None:
        """Re-pin a plan to a new head whose diff is identical to its old one."""
        pull_request = await PullRequest.get(owner, repo, number)
        if pull_request is None:
            return
        async with postgres.session() as session:
            row = await session.scalar(
                select(cls)
                .where(cls.pull_request_id == pull_request.id, cls.head_sha == from_sha)
                .with_for_update()
            )
            if row is not None:
                row.head_sha = to_sha
                row.plan_json = row.plan.model_copy(update={"head_sha": to_sha}).model_dump(
                    mode="json"
                )
            await session.commit()


class WalkthroughFileView(BaseModel):
    path: str
    added: list[LineRange]
    deleted: list[LineRange]


class WalkthroughStepView(BaseModel):
    index: int
    title: str
    summary: str
    other: bool
    files: list[WalkthroughFileView]


class WalkthroughView(BaseModel):
    """A complete plan as the review page and the reviewer read it, Other last."""

    head_sha: str
    human_input: str
    steps: list[WalkthroughStepView]

    @staticmethod
    def _files(refs: list[LineRef], textless: Sequence[str] = ()) -> list[WalkthroughFileView]:
        files = [
            WalkthroughFileView(path=path, added=added, deleted=deleted)
            for path, (added, deleted) in LineRef.ranges(refs).items()
        ]
        files += [WalkthroughFileView(path=path, added=[], deleted=[]) for path in textless]
        return sorted(files, key=lambda file: file.path)

    @classmethod
    def of(cls, walkthrough: Walkthrough) -> Self:
        plan = walkthrough.plan
        steps = [
            WalkthroughStepView(
                index=position,
                title=chunk.title,
                summary=chunk.explanation,
                other=False,
                files=cls._files(chunk.lines),
            )
            for position, chunk in enumerate(plan.chunks, start=1)
        ]
        if plan.other or plan.other_files:
            steps.append(
                WalkthroughStepView(
                    index=len(steps) + 1,
                    title=OTHER_TITLE,
                    summary=plan.other_summary,
                    other=True,
                    files=cls._files(plan.other, plan.other_files),
                )
            )
        return cls(
            head_sha=walkthrough.head_sha,
            human_input=walkthrough.human_input_summary,
            steps=steps,
        )

    @classmethod
    async def for_head(cls, owner: str, repo: str, number: int, head_sha: str) -> Self | None:
        """The walkthrough for the PR's current head, or ``None`` without one or a database."""
        if not head_sha or not postgres.configured():
            return None
        walkthrough = await Walkthrough.for_head(owner, repo, number, head_sha)
        return cls.of(walkthrough) if walkthrough else None
