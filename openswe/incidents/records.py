"""Incident state in PostgreSQL.

Each JSONB column holds its record's ``model_dump`` and is read back through the
model; the typed columns beside it are what queries filter on. ``updated_at`` is
when PostgreSQL last wrote the row, which the Store import compares against.
"""

import logging
from collections.abc import Collection, Iterable
from datetime import UTC, datetime
from uuid import UUID, uuid7

from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.incidents.document_models import IncidentHistory
from openswe.incidents.models import (
    CommandReceipt,
    Incident,
    IncidentId,
    IncidentPolicy,
    IncidentReportRecord,
)
from openswe.ui_invalidations import Topic
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

# The conflict target of ``incident_policy_singleton``, the unique index on ``(true)``.
_ONE_POLICY = text("(true)")


class IncidentPolicyRow(Base):
    __tablename__ = "incident_policy"

    version: Mapped[int]
    policy: Mapped[JsonObject] = mapped_column(JSONB)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


class IncidentRow(Base):
    __tablename__ = "incident"

    incident_id: Mapped[str]
    workspace_id: Mapped[str]
    channel_id: Mapped[str]
    thread_id: Mapped[str]
    status: Mapped[str]
    incident: Mapped[JsonObject] = mapped_column(JSONB)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


class IncidentReportRow(Base):
    __tablename__ = "incident_report"

    incident_id: Mapped[str]
    report: Mapped[JsonObject] = mapped_column(JSONB)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


class IncidentHistoryRow(Base):
    __tablename__ = "incident_history"

    incident_id: Mapped[str]
    history: Mapped[JsonObject] = mapped_column(JSONB)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


class IncidentPostmortemRow(Base):
    __tablename__ = "incident_postmortem"

    incident_id: Mapped[str]
    markdown: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


class IncidentCommandRow(Base):
    __tablename__ = "incident_command"

    claim_id: Mapped[str]
    incident_id: Mapped[str]
    action: Mapped[str]
    content_hash: Mapped[str]
    copied_from_store: Mapped[bool] = mapped_column(default=False)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


def _readable[RecordT: BaseModel](
    model: type[RecordT], values: Iterable[JsonObject]
) -> list[RecordT]:
    """Validate each value, skipping unreadable ones so they cannot hide the rest."""
    records: list[RecordT] = []
    for value in values:
        try:
            records.append(model.model_validate(value))
        except ValidationError:
            logger.warning(
                "Skipping unreadable incident record",
                extra={"incident_record": model.__name__},
                exc_info=True,
            )
    return records


class IncidentPolicies:
    """The one incident policy."""

    async def get(self) -> IncidentPolicy | None:
        async with postgres.session() as session:
            value = await session.scalar(select(IncidentPolicyRow.policy))
        return None if value is None else IncidentPolicy.model_validate(value)

    async def put(self, policy: IncidentPolicy) -> IncidentPolicy:
        value = policy.model_dump(mode="json")
        async with postgres.session() as session:
            await session.execute(
                insert(IncidentPolicyRow)
                .values(id=uuid7(), version=policy.version, policy=value)
                .on_conflict_do_update(
                    index_elements=[_ONE_POLICY],
                    set_={
                        "version": policy.version,
                        "policy": value,
                        "updated_at": func.clock_timestamp(),
                    },
                )
            )
            await Topic.INCIDENT_SETTINGS.invalidate(session)
        return policy

    async def written_at(self) -> datetime | None:
        async with postgres.session() as session:
            return await session.scalar(select(IncidentPolicyRow.updated_at))


class Incidents:
    """Every enrolled channel's incident, by incident id."""

    async def get(self, id: IncidentId) -> Incident | None:
        async with postgres.session() as session:
            value = await session.scalar(
                select(IncidentRow.incident).where(IncidentRow.incident_id == id)
            )
        return None if value is None else Incident.model_validate(value)

    async def put(self, id: IncidentId, record: Incident) -> Incident:
        columns = {
            "workspace_id": record.workspace_id,
            "channel_id": record.channel_id,
            "thread_id": record.thread_id,
            "status": record.status,
            "incident": record.model_dump(mode="json"),
        }
        async with postgres.session() as session:
            await session.execute(
                insert(IncidentRow)
                .values(id=uuid7(), incident_id=id, **columns)
                .on_conflict_do_update(
                    index_elements=[IncidentRow.incident_id],
                    set_={**columns, "updated_at": func.clock_timestamp()},
                )
            )
            await Topic.INCIDENTS.invalidate(session, key=id)
        return record

    async def for_thread(self, thread_id: str) -> Incident | None:
        """The incident whose agent runs on ``thread_id``."""
        async with postgres.session() as session:
            values = await session.scalars(
                select(IncidentRow.incident).where(IncidentRow.thread_id == thread_id).limit(1)
            )
            return next(iter(_readable(Incident, values)), None)

    async def any(self) -> bool:
        async with postgres.session() as session:
            return await session.scalar(select(IncidentRow.id).limit(1)) is not None

    async def in_workspace(
        self, workspace_id: str, statuses: Collection[str] | None = None
    ) -> list[Incident]:
        """The Slack workspace's incidents, only those in ``statuses`` when given."""
        query = select(IncidentRow.incident).where(IncidentRow.workspace_id == workspace_id)
        if statuses is not None:
            query = query.where(IncidentRow.status.in_(statuses))
        async with postgres.session() as session:
            return _readable(Incident, await session.scalars(query))

    async def written_at(self, id: IncidentId) -> datetime | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(IncidentRow.updated_at).where(IncidentRow.incident_id == id)
            )


class IncidentReports:
    """Each incident's latest report, by incident id."""

    async def get(self, incident_id: IncidentId) -> IncidentReportRecord | None:
        async with postgres.session() as session:
            value = await session.scalar(
                select(IncidentReportRow.report).where(IncidentReportRow.incident_id == incident_id)
            )
        return None if value is None else IncidentReportRecord.model_validate(value)

    async def put(
        self, incident_id: IncidentId, record: IncidentReportRecord
    ) -> IncidentReportRecord:
        value = record.model_dump(mode="json")
        async with postgres.session() as session:
            await session.execute(
                insert(IncidentReportRow)
                .values(id=uuid7(), incident_id=incident_id, report=value)
                .on_conflict_do_update(
                    index_elements=[IncidentReportRow.incident_id],
                    set_={"report": value, "updated_at": func.clock_timestamp()},
                )
            )
            await Topic.INCIDENTS.invalidate(session, key=incident_id)
        return record

    async def written_at(self, incident_id: IncidentId) -> datetime | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(IncidentReportRow.updated_at).where(
                    IncidentReportRow.incident_id == incident_id
                )
            )


class IncidentHistories:
    """Curated history of every incident, by incident id."""

    async def get(self, id: IncidentId) -> IncidentHistory | None:
        async with postgres.session() as session:
            value = await session.scalar(
                select(IncidentHistoryRow.history).where(IncidentHistoryRow.incident_id == id)
            )
        return None if value is None else IncidentHistory.model_validate(value)

    async def put(self, id: IncidentId, history: IncidentHistory) -> IncidentHistory:
        value = history.model_dump(mode="json")
        async with postgres.session() as session:
            await session.execute(
                insert(IncidentHistoryRow)
                .values(id=uuid7(), incident_id=id, history=value)
                .on_conflict_do_update(
                    index_elements=[IncidentHistoryRow.incident_id],
                    set_={"history": value, "updated_at": func.clock_timestamp()},
                )
            )
            await Topic.INCIDENTS.invalidate(session, key=id)
        return history

    async def all(self) -> list[IncidentHistory]:
        async with postgres.session() as session:
            return _readable(
                IncidentHistory, await session.scalars(select(IncidentHistoryRow.history))
            )

    async def written_at(self, id: IncidentId) -> datetime | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(IncidentHistoryRow.updated_at).where(IncidentHistoryRow.incident_id == id)
            )


class IncidentPostmortems:
    """Each incident's latest agent postmortem, as Markdown, by incident id."""

    async def get(self, incident_id: IncidentId) -> str | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(IncidentPostmortemRow.markdown).where(
                    IncidentPostmortemRow.incident_id == incident_id
                )
            )

    async def put(self, incident_id: IncidentId, markdown: str) -> None:
        async with postgres.session() as session:
            await session.execute(
                insert(IncidentPostmortemRow)
                .values(id=uuid7(), incident_id=incident_id, markdown=markdown)
                .on_conflict_do_update(
                    index_elements=[IncidentPostmortemRow.incident_id],
                    set_={"markdown": markdown, "updated_at": func.clock_timestamp()},
                )
            )
            await Topic.INCIDENTS.invalidate(session, key=incident_id)

    async def written_at(self, incident_id: IncidentId) -> datetime | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(IncidentPostmortemRow.updated_at).where(
                    IncidentPostmortemRow.incident_id == incident_id
                )
            )


class CommandReceipts:
    """Responder command receipts by claim id, each claimed before its command runs."""

    async def get(self, claim_id: str) -> CommandReceipt | None:
        async with postgres.session() as session:
            row = await session.scalar(
                select(IncidentCommandRow).where(IncidentCommandRow.claim_id == claim_id)
            )
        if row is None:
            return None
        return CommandReceipt(
            id=row.claim_id,
            incident_id=IncidentId(row.incident_id),
            action=row.action,
            content_hash=row.content_hash,
            created_at=(row.created_at or datetime.now(UTC)).isoformat(),
        )

    async def claim(self, receipt: CommandReceipt, *, store_copy: bool = False) -> bool:
        """Write ``receipt`` unless its claim id is taken; whether this call wrote it."""
        async with postgres.session() as session:
            claimed = await session.scalar(
                insert(IncidentCommandRow)
                .values(
                    id=uuid7(),
                    claim_id=receipt.id,
                    incident_id=receipt.incident_id,
                    action=receipt.action,
                    content_hash=receipt.content_hash,
                    copied_from_store=store_copy,
                )
                .on_conflict_do_nothing(index_elements=[IncidentCommandRow.claim_id])
                .returning(IncidentCommandRow.id)
            )
        return claimed is not None

    async def delete(self, claim_id: str) -> None:
        async with postgres.session() as session:
            await session.execute(
                delete(IncidentCommandRow).where(IncidentCommandRow.claim_id == claim_id)
            )

    async def written_at(self, claim_id: str) -> datetime | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(IncidentCommandRow.created_at).where(IncidentCommandRow.claim_id == claim_id)
            )

    async def delete_store_copies(self, present: Collection[str], copied_after: datetime) -> int:
        """Delete receipts the Store import copied after ``copied_after`` whose Store record,
        keyed by claim id in ``present``, is gone.

        A replica of the previous release deletes a receipt only from the Store.
        """
        async with postgres.session() as session:
            copies = await session.execute(
                select(IncidentCommandRow.id, IncidentCommandRow.claim_id).where(
                    IncidentCommandRow.copied_from_store,
                    IncidentCommandRow.created_at > copied_after,
                )
            )
            gone = [id for id, claim_id in copies.tuples() if claim_id not in present]
            if gone:
                await session.execute(
                    delete(IncidentCommandRow).where(
                        IncidentCommandRow.id.in_(gone), IncidentCommandRow.copied_from_store
                    )
                )
            return len(gone)
