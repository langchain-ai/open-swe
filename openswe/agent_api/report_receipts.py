"""Which of an MDA run's reports the backend has recorded, by the key the run gave each.

A run sends its reports and notices in batches (``runtime_api.reports``) and sends
again whatever it did not hear was taken, to whichever replica answers. Each key is
recorded once: a delivery claims it before recording the report and keeps the reply
once it has, so a later delivery of the key answers with that reply instead. A
claim whose delivery died is taken over once it is older than any delivery runs.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID, uuid7

from pydantic import JsonValue
from sqlalchemy import ForeignKey, delete, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.users.models import User

logger = logging.getLogger(__name__)

# A delivery answers within its call's timeout, or the run sends it again.
_CLAIM_LEASE: Final = timedelta(minutes=10)
# Far longer than a run retries a report, and long enough to cover a run resumed from
# its checkpoint the next day.
RETAINED: Final = timedelta(days=7)
PURGE_JOB: Final = "agent_report_receipt_purge"
PURGE_SCHEDULE: Final = "23 4 * * *"
# Small enough that each delete is a short transaction and a short burst of WAL.
_PURGE_BATCH: Final = 5_000


class ReportReceipt(Base):
    __tablename__ = "agent_report_receipt"

    thread_id: Mapped[str]
    idempotency_key: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    # What the report's endpoint answered; NULL while a delivery records it.
    reply: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB, default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )


@dataclass(frozen=True)
class Recorded:
    """A delivery already recorded the report; this is what it answered."""

    reply: dict[str, JsonValue]


@dataclass(frozen=True)
class Recording:
    """Another delivery is recording the report right now."""


@dataclass(frozen=True)
class Claimed:
    """This delivery records the report, then calls ``record`` or ``release``."""

    id: UUID


type Claim = Claimed | Recorded | Recording


class ReportReceipts:
    """One thread's recorded reports."""

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id

    async def claim(self, key: str) -> Claim:
        if not postgres.configured():
            return Claimed(uuid7())
        cls = ReportReceipt
        async with postgres.session() as session:
            claimed = await session.scalar(
                insert(cls)
                .values(id=uuid7(), thread_id=self.thread_id, idempotency_key=key)
                .on_conflict_do_update(
                    index_elements=[cls.thread_id, cls.idempotency_key],
                    set_={"updated_at": NOW},
                    where=cls.reply.is_(None) & (cls.updated_at < func.now() - _CLAIM_LEASE),
                )
                .returning(cls.id)
            )
            if claimed is not None:
                return Claimed(claimed)
            reply = await session.scalar(
                select(cls.reply).where(cls.thread_id == self.thread_id, cls.idempotency_key == key)
            )
        return Recording() if reply is None else Recorded(reply)

    async def record(self, claim: Claimed, reply: dict[str, JsonValue]) -> None:
        if not postgres.configured():
            return
        async with postgres.session() as session:
            await session.execute(
                update(ReportReceipt).where(ReportReceipt.id == claim.id).values(reply=reply)
            )

    async def keep(self, claim: Claimed, key: str, reply: dict[str, JsonValue]) -> None:
        """Keep ``reply`` as what ``key`` answered, or else give the key up.

        The report is recorded either way. A key left claimed would hold every report
        behind it until the lease runs out, and then be recorded again anyway; given
        up, only a delivery sent again after this one's answer was lost records it again.
        """
        try:
            await self.record(claim, reply)
        except BaseException as exc:
            await asyncio.shield(self.release(claim))
            if not isinstance(exc, Exception):
                raise
            logger.exception(
                "An MDA report's reply was not kept; its key was given up",
                extra={"agent_thread_id": self.thread_id, "report_key": key},
            )

    async def release(self, claim: Claimed) -> None:
        """Give the key up unrecorded, so the next delivery records it."""
        if not postgres.configured():
            return
        async with postgres.session() as session:
            await session.execute(
                delete(ReportReceipt).where(
                    ReportReceipt.id == claim.id, ReportReceipt.reply.is_(None)
                )
            )


async def purge_report_receipts() -> int:
    """Forget the keys of reports recorded longer ago than any run sends one again.

    Deletes in batches, each its own transaction, so a day's worth of keys never
    holds one long transaction or lands as one burst of WAL.
    """
    cls = ReportReceipt
    cutoff = func.now() - RETAINED
    purged = 0
    while True:
        async with postgres.transaction() as conn:
            result = await conn.execute(
                delete(cls).where(
                    cls.id.in_(
                        select(cls.id)
                        .where(cls.created_at < cutoff)
                        .order_by(cls.created_at)
                        .limit(_PURGE_BATCH)
                        .with_for_update(skip_locked=True)
                    )
                )
            )
        purged += result.rowcount
        if result.rowcount < _PURGE_BATCH:
            return purged
