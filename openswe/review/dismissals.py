"""People dismissing the reviewer's findings from the dashboard, and why."""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid7

from pydantic import BaseModel, Field
from sqlalchemy import ForeignKey, ForeignKeyConstraint, ScalarSelect
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database.orm import NOW, Base
from openswe.github.http import GitHubClient
from openswe.github.pull_request_actions import ResolveReviewThreads, resolve_review_threads
from openswe.review.findings import (
    Finding,
    ReviewerThreadMissingError,
    ReviewerThreadUnlinkedError,
    mutate_findings,
    thread_ids_for_finding,
)
from openswe.thread_ids import reviewer_thread_id
from openswe.threads import index
from openswe.users.models import User
from openswe.utils.reviewer_outcomes import FALSE_POSITIVE, upsert_finding_outcome

logger = logging.getLogger(__name__)


class FindingDismissal(Base):
    __tablename__ = "pull_request_finding_dismissal"
    __table_args__ = (
        ForeignKeyConstraint(
            ["pull_request_id", "finding_id"],
            ["pull_request_finding.pull_request_id", "pull_request_finding.id"],
            ondelete="CASCADE",
        ),
    )

    pull_request_id: Mapped[UUID]
    finding_id: Mapped[str]
    reason: Mapped[str | None] = mapped_column(default=None)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )


class DismissalRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=3000)


class FindingNotFound(LookupError):
    """The pull request has no reviewer finding with that id."""


class FindingSettled(ValueError):
    """The finding is already resolved or dismissed."""


@dataclass
class FindingDismisser:
    """One person dismissing one of the reviewer's findings on a pull request."""

    owner: str
    repo: str
    number: int
    finding_id: str
    login: str
    user_id: UUID | ScalarSelect[UUID] | None
    reason: str | None
    dismissed: Finding | None = field(default=None, init=False)
    settled_as: str | None = field(default=None, init=False)

    async def dismiss(self) -> Finding:
        """Mark the finding dismissed and record why, then resolve its GitHub threads."""
        thread_id = await index.current_thread_id(
            reviewer_thread_id(self.owner, self.repo, self.number)
        )
        try:
            await mutate_findings(thread_id, self._apply, alongside=self._record)
        except (ReviewerThreadMissingError, ReviewerThreadUnlinkedError) as exc:
            raise FindingNotFound(self.finding_id) from exc
        if self.settled_as is not None:
            raise FindingSettled(self.settled_as)
        if self.dismissed is None:
            raise FindingNotFound(self.finding_id)
        await self._resolve_on_github(self.dismissed)
        await upsert_finding_outcome(
            self.dismissed,
            label=FALSE_POSITIVE,
            label_source="dismissed_by_person",
            repo=f"{self.owner}/{self.repo}",
            pr_number=self.number,
            pr_url=f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}",
            thread_id=thread_id,
        )
        return self.dismissed

    @property
    def note(self) -> str:
        return (
            f"Dismissed by @{self.login}: {self.reason}"
            if self.reason
            else f"Dismissed by @{self.login}"
        )

    def _apply(self, findings: list[Finding]) -> bool:
        finding = next((item for item in findings if item["id"] == self.finding_id), None)
        if finding is None:
            return False
        if (status := finding.get("status", "open")) != "open":
            self.settled_as = status
            return False
        finding["status"] = "dismissed"
        finding["resolution_note"] = self.note
        self.dismissed = finding
        return True

    async def _record(self, session: AsyncSession, pull_request_id: UUID) -> None:
        await session.execute(
            insert(FindingDismissal).values(
                id=uuid7(),
                pull_request_id=pull_request_id,
                finding_id=self.finding_id,
                reason=self.reason,
                created_by=self.user_id,
                updated_by=self.user_id,
            )
        )

    async def _resolve_on_github(self, finding: Finding) -> None:
        """Resolve the finding's GitHub threads as the person; the dismissal stands if this fails."""
        thread_ids = thread_ids_for_finding(finding)
        if not thread_ids:
            return
        extra = {
            "pr_repo_full_name": f"{self.owner}/{self.repo}",
            "pr_number": self.number,
            "finding_id": self.finding_id,
        }
        try:
            async with GitHubClient.as_user(self.login) as github:
                result = await resolve_review_threads(
                    github.repo(self.owner, self.repo).pull_request(self.number),
                    ResolveReviewThreads(thread_ids=thread_ids),
                )
        except Exception:
            logger.warning(
                "Could not resolve a dismissed finding's GitHub threads", extra=extra, exc_info=True
            )
            return
        if result.failed:
            logger.warning(
                "GitHub left a dismissed finding's threads open",
                extra={**extra, "review_thread_ids": result.failed},
            )
