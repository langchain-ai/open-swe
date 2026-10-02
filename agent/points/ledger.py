"""Points: an append-only ledger of every point awarded or taken, and why.

A total is the sum of a person's entries, so the ledger is also the audit trail.
What each entry is about lives in ``details``, shaped by its reason.
"""

import logging
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid7

from pydantic import BaseModel
from sqlalchemy import ForeignKey, Text, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.utils.json_types import JsonObject

logger = logging.getLogger(__name__)


class Reviewed(BaseModel):
    """The first review of a pull request on GitHub, assigned or not."""

    reason: Literal["reviewed"] = "reviewed"
    repository: str
    pr_number: int


class PickExpired(BaseModel):
    """Open SWE picked them to review and they did not accept in time."""

    reason: Literal["pick_expired"] = "pick_expired"
    repository: str
    pr_number: int
    request_id: UUID


PointDetails = Reviewed | PickExpired
PointReason = Literal["reviewed", "pick_expired"]
POINTS: dict[PointReason, int] = {"reviewed": 1, "pick_expired": -1}


class Point(Base):
    __tablename__ = "point"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    delta: Mapped[int]
    reason: Mapped[PointReason] = mapped_column(Text)
    details: Mapped[JsonObject] = mapped_column(JSONB, default_factory=dict)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    @classmethod
    async def award(cls, user_id: UUID, details: PointDetails) -> bool:
        """Write one ledger entry; ``False`` when the same award was already recorded."""
        delta = POINTS[details.reason]
        statement = (
            insert(cls)
            .values(
                id=uuid7(),
                user_id=user_id,
                delta=delta,
                reason=details.reason,
                details=details.model_dump(mode="json", exclude={"reason"}),
            )
            .on_conflict_do_nothing()
            .returning(cls.id)
        )
        async with postgres.session() as session:
            recorded = await session.scalar(statement) is not None
        if recorded:
            logger.info(
                "Recorded points",
                extra={"user_id": str(user_id), "point_reason": details.reason, "delta": delta},
            )
        return recorded

    @classmethod
    async def missed_pick_user_ids(cls, request_id: UUID) -> set[UUID]:
        """Everyone a review request rotated away from for not accepting."""
        async with postgres.session() as session:
            rows = await session.scalars(
                select(cls.user_id).where(
                    cls.reason == "pick_expired",
                    cls.details["request_id"].astext == str(request_id),
                )
            )
            return set(rows.all())
