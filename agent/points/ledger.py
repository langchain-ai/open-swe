"""Points: an append-only ledger of every point awarded or taken, and why.

A total is the sum of a person's entries, so the ledger is also the audit trail.
"""

import logging
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid7

from sqlalchemy import BigInteger, ForeignKey, Text, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from agent.database import postgres
from agent.database.orm import NOW, Base

logger = logging.getLogger(__name__)

PointReason = Literal["reviewed", "pick_expired"]
POINTS: dict[PointReason, int] = {
    # The first review of a pull request on GitHub, assigned or not.
    "reviewed": 1,
    # Open SWE picked them to review and they did not accept in time.
    "pick_expired": -1,
}


class Point(Base):
    __tablename__ = "point"

    github_id: Mapped[int] = mapped_column(BigInteger)
    github_login: Mapped[str]
    delta: Mapped[int]
    reason: Mapped[PointReason] = mapped_column(Text)
    repository_key: Mapped[str | None] = mapped_column(default=None)
    pr_number: Mapped[int | None] = mapped_column(default=None)
    request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("human_review_request.id", ondelete="SET NULL"), default=None
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    @classmethod
    async def award(
        cls,
        *,
        github_id: int,
        github_login: str,
        reason: PointReason,
        repository_key: str | None = None,
        pr_number: int | None = None,
        request_id: UUID | None = None,
    ) -> bool:
        """Write one ledger entry; ``False`` when the same award was already recorded."""
        delta = POINTS[reason]
        statement = (
            insert(cls)
            .values(
                id=uuid7(),
                github_id=github_id,
                github_login=github_login,
                delta=delta,
                reason=reason,
                repository_key=repository_key.lower() if repository_key else None,
                pr_number=pr_number,
                request_id=request_id,
            )
            .on_conflict_do_nothing()
            .returning(cls.id)
        )
        async with postgres.session() as session:
            recorded = await session.scalar(statement) is not None
        if recorded:
            logger.info(
                "Recorded points",
                extra={
                    "github_login": github_login,
                    "point_reason": reason,
                    "delta": delta,
                    "repository": repository_key or "",
                    "pr_number": pr_number,
                },
            )
        return recorded

    @classmethod
    async def missed_pick_ids(cls, request_id: UUID) -> set[int]:
        """GitHub ids of everyone a review request rotated away from for not accepting."""
        async with postgres.session() as session:
            rows = await session.scalars(
                select(cls.github_id).where(
                    cls.request_id == request_id, cls.reason == "pick_expired"
                )
            )
            return set(rows.all())
