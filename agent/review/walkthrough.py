"""The review scout's reading order for a pull request.

The scout re-commits a PR's full diff as a series of steps a reviewer can read
top to bottom. Each step owns the changed lines it committed, attributed back
to the PR's own diff: added lines in head numbering, deleted lines in
merge-base numbering. Those are the numbers the review page, findings and
comments already use, so a step can be rendered as a filtered view of the
PR's real hunks.
"""

import logging
from datetime import datetime
from typing import Self
from uuid import UUID, uuid7

from pydantic import BaseModel
from sqlalchemy import ForeignKey, delete, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.github.pull_requests import PullRequest
from agent.github.repositories import Repository

logger = logging.getLogger(__name__)

LineRange = tuple[int, int]


class FileLines(BaseModel):
    """The lines of one file a step owns, as inclusive ranges."""

    path: str
    added: list[LineRange] = []
    deleted: list[LineRange] = []


class StepDraft(BaseModel):
    """One step as the scout produced it, before it is stored."""

    title: str
    summary: str = ""
    is_other: bool = False
    files: list[FileLines] = []


class WalkthroughFile(Base):
    __tablename__ = "pull_request_walkthrough_file"

    path: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    step_id: Mapped[UUID] = mapped_column(
        ForeignKey("pull_request_walkthrough_step.id", ondelete="CASCADE"), init=False
    )
    added_lines: Mapped[list[list[int]]] = mapped_column(JSONB, default_factory=list)
    deleted_lines: Mapped[list[list[int]]] = mapped_column(JSONB, default_factory=list)


class WalkthroughStep(Base):
    __tablename__ = "pull_request_walkthrough_step"

    position: Mapped[int]
    title: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    pull_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("pull_request_walkthrough.pull_request_id", ondelete="CASCADE"), init=False
    )
    summary: Mapped[str] = mapped_column(default="")
    is_other: Mapped[bool] = mapped_column(default=False)
    files: Mapped[list[WalkthroughFile]] = relationship(
        default_factory=list,
        cascade="all, delete-orphan",
        order_by=lambda: WalkthroughFile.path,
    )


class Walkthrough(Base):
    """The newest scout result for a pull request, pinned to the head it describes."""

    __tablename__ = "pull_request_walkthrough"

    pull_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("pull_request.id", ondelete="CASCADE"), primary_key=True
    )
    head_sha: Mapped[str]
    merge_base_sha: Mapped[str]
    scout_thread_id: Mapped[str] = mapped_column(default="")
    human_input_summary: Mapped[str] = mapped_column(server_default="", default="")
    generated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    steps: Mapped[list[WalkthroughStep]] = relationship(
        default_factory=list,
        cascade="all, delete-orphan",
        order_by=lambda: WalkthroughStep.position,
    )

    @classmethod
    async def replace(
        cls,
        owner: str,
        repo: str,
        number: int,
        *,
        head_sha: str,
        merge_base_sha: str,
        scout_thread_id: str,
        steps: list[StepDraft],
        human_input_summary: str = "",
    ) -> None:
        """Store ``steps`` as this PR's walkthrough, replacing any earlier one."""
        pull_request = await PullRequest(owner=owner, repo=repo, number=number).ensure()
        walkthrough = cls(
            pull_request_id=pull_request.id,
            head_sha=head_sha,
            merge_base_sha=merge_base_sha,
            scout_thread_id=scout_thread_id,
            human_input_summary=human_input_summary,
            steps=[
                WalkthroughStep(
                    position=position,
                    title=step.title,
                    summary=step.summary,
                    is_other=step.is_other,
                    files=[
                        WalkthroughFile(
                            path=file.path,
                            added_lines=[list(r) for r in file.added],
                            deleted_lines=[list(r) for r in file.deleted],
                        )
                        for file in step.files
                    ],
                )
                for position, step in enumerate(steps)
            ],
        )
        async with postgres.session() as session:
            await session.execute(delete(cls).where(cls.pull_request_id == pull_request.id))
            session.add(walkthrough)
            await session.commit()

    @classmethod
    async def for_head(cls, owner: str, repo: str, number: int, head_sha: str) -> Self | None:
        """The walkthrough describing ``head_sha``, or ``None`` when there is none yet."""
        async with postgres.session() as session:
            return await session.scalar(
                select(cls)
                .join(PullRequest, PullRequest.id == cls.pull_request_id)
                .join(PullRequest.repository)
                .where(
                    Repository.key == f"{owner}/{repo}".lower(),
                    PullRequest.number == number,
                    cls.head_sha == head_sha,
                )
                .options(selectinload(cls.steps).selectinload(WalkthroughStep.files))
            )

    @classmethod
    async def generated_since(cls, owner: str, repo: str, number: int, since: datetime) -> bool:
        """Whether the PR has a walkthrough, for any head, stored at or after ``since``."""
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
                    cls.generated_at >= since,
                )
            )
        return found is not None

    @classmethod
    async def dismiss(cls, owner: str, repo: str, number: int) -> bool:
        """Delete the PR's walkthrough so the scout can build it again; ``False`` when none existed."""
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
    async def carry_forward(
        cls, owner: str, repo: str, number: int, *, from_sha: str, to_sha: str
    ) -> None:
        """Re-pin a walkthrough to a new head whose diff is identical to its old one."""
        pull_request = await PullRequest.get(owner, repo, number)
        if pull_request is None:
            return
        async with postgres.session() as session:
            await session.execute(
                update(cls)
                .where(cls.pull_request_id == pull_request.id, cls.head_sha == from_sha)
                .values(head_sha=to_sha)
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
    """A walkthrough as the review page reads it."""

    head_sha: str
    human_input: str
    steps: list[WalkthroughStepView]

    @classmethod
    def of(cls, walkthrough: Walkthrough) -> Self:
        return cls(
            head_sha=walkthrough.head_sha,
            human_input=walkthrough.human_input_summary,
            steps=[
                WalkthroughStepView(
                    index=position,
                    title=step.title,
                    summary=step.summary,
                    other=step.is_other,
                    files=[
                        WalkthroughFileView(
                            path=file.path,
                            added=[(start, end) for start, end in file.added_lines],
                            deleted=[(start, end) for start, end in file.deleted_lines],
                        )
                        for file in step.files
                    ],
                )
                for position, step in enumerate(walkthrough.steps, start=1)
            ],
        )

    @classmethod
    async def for_head(cls, owner: str, repo: str, number: int, head_sha: str) -> Self | None:
        """The walkthrough for the PR's current head, or ``None`` without one or a database."""
        if not head_sha or not postgres.configured():
            return None
        walkthrough = await Walkthrough.for_head(owner, repo, number, head_sha)
        return cls.of(walkthrough) if walkthrough else None
