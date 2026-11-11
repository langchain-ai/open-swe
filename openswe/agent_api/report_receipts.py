"""Which of an MDA run's reports the backend has recorded, by the key the run gave each.

A run sends its reports and notices in batches (``runtime_api.reports``) and sends
again whatever it did not hear was taken, to whichever replica answers. Each key is
recorded once: a delivery claims it before recording the report and keeps the reply
once it has, so a later delivery of the key answers with that reply instead. A
claim whose delivery died is taken over once it is older than any delivery runs.
A delivery claims, keeps and gives up a batch's keys together, a statement each.
"""

import asyncio
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID, uuid7

from pydantic import JsonValue
from sqlalchemy import ForeignKey, bindparam, delete, func, select, update
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

    async def claim(self, keys: Iterable[str]) -> dict[str, Claim]:
        """Claim every key of a batch at once, or learn who has each."""
        # In one order, so two deliveries of overlapping batches lock their rows alike.
        wanted = sorted(set(keys))
        if not wanted:
            return {}
        cls = ReportReceipt
        async with postgres.transaction() as conn:
            claimed = await conn.execute(
                insert(cls)
                .values(
                    [
                        {"id": uuid7(), "thread_id": self.thread_id, "idempotency_key": key}
                        for key in wanted
                    ]
                )
                .on_conflict_do_update(
                    index_elements=[cls.thread_id, cls.idempotency_key],
                    set_={"updated_at": NOW},
                    where=cls.reply.is_(None) & (cls.updated_at < func.now() - _CLAIM_LEASE),
                )
                .returning(cls.idempotency_key, cls.id)
            )
            claims: dict[str, Claim] = {key: Claimed(id) for key, id in claimed}
            held = [key for key in wanted if key not in claims]
            if held:
                replies = await conn.execute(
                    select(cls.idempotency_key, cls.reply).where(
                        cls.thread_id == self.thread_id, cls.idempotency_key.in_(held)
                    )
                )
                for key, reply in replies:
                    claims[key] = Recording() if reply is None else Recorded(reply)
        # A key whose receipt was purged since is another delivery's to take.
        return {key: claims.get(key, Recording()) for key in wanted}

    async def record(self, kept: Sequence[tuple[Claimed, dict[str, JsonValue]]]) -> None:
        if not kept:
            return
        cls = ReportReceipt
        async with postgres.transaction() as conn:
            await conn.execute(
                update(cls)
                .where(cls.id == bindparam("claim_id"))
                .values(reply=bindparam("kept_reply")),
                [{"claim_id": claim.id, "kept_reply": reply} for claim, reply in kept],
            )

    async def keep(self, kept: Sequence[tuple[Claimed, str, dict[str, JsonValue]]]) -> None:
        """Keep each reply as what its key answered, or else give the keys up.

        The reports are recorded either way. A key left claimed would hold every report
        behind it until the lease runs out, and then be recorded again anyway; given
        up, only a delivery sent again after this one's answer was lost records it again.
        """
        try:
            await self.record([(claim, reply) for claim, _, reply in kept])
        except BaseException as exc:
            await asyncio.shield(self.release(claim for claim, _, _ in kept))
            if not isinstance(exc, Exception):
                raise
            logger.exception(
                "MDA reports' replies were not kept; their keys were given up",
                extra={"agent_thread_id": self.thread_id, "report_keys": [k for _, k, _ in kept]},
            )

    async def release(self, claims: Iterable[Claimed]) -> None:
        """Give the keys up unrecorded, so the next delivery records them."""
        ids = [claim.id for claim in claims]
        if not ids:
            return
        async with postgres.transaction() as conn:
            await conn.execute(
                delete(ReportReceipt).where(
                    ReportReceipt.id.in_(ids), ReportReceipt.reply.is_(None)
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
