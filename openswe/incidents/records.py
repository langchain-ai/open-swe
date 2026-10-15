"""Incident state in PostgreSQL.

Each incident's row also keeps its latest report, curated history, and
postmortem, so each is written as a column of an existing row. Each JSONB
column holds its record's ``model_dump`` and is read back through the model;
the typed columns beside it are what queries filter on. ``updated_at`` is when
PostgreSQL last wrote the row.
"""

import logging
from collections.abc import Collection, Iterable, Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid7

from pydantic import BaseModel, ValidationError
from sqlalchemy import ForeignKey, ScalarSelect, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import InstrumentedAttribute, Mapped, mapped_column

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
from openswe.slack.channels import SlackChannel
from openswe.ui_invalidations import Topic
from openswe.users.records import user_id_for_login
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

# The conflict target of ``incident_policy_singleton``, the unique index on ``(true)``.
_ONE_POLICY = text("(true)")


class IncidentPolicyRow(Base):
    __tablename__ = "incident_policy"

    version: Mapped[int]
    policy: Mapped[JsonObject] = mapped_column(JSONB)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )


class IncidentRow(Base):
    __tablename__ = "incident"

    external_id: Mapped[str]
    slack_team_id: Mapped[str]
    channel_id: Mapped[str] = mapped_column(ForeignKey("slack_channel.id"))
    thread_id: Mapped[str]
    status: Mapped[str]
    incident: Mapped[JsonObject] = mapped_column(JSONB)
    report: Mapped[JsonObject | None] = mapped_column(JSONB, default=None)
    history: Mapped[JsonObject | None] = mapped_column(JSONB, default=None)
    postmortem: Mapped[str | None] = mapped_column(default=None)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )


class IncidentCommandRow(Base):
    __tablename__ = "incident_command"

    claim_id: Mapped[str]
    incident: Mapped[UUID] = mapped_column(ForeignKey("incident.id", ondelete="CASCADE"))
    action: Mapped[str]
    content_hash: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )


def _person(login: str | None) -> ScalarSelect[UUID] | None:
    """The ``users.id`` of the person with GitHub ``login``; ``None`` for a system write."""
    return user_id_for_login(login) if login else None


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


class MissingIncident(LookupError):
    """No incident row to keep a report, history, postmortem, or command receipt on."""

    def __init__(self, incident_id: str) -> None:
        super().__init__(f"No incident {incident_id!r} to update")


async def _part[ValueT](
    column: InstrumentedAttribute[ValueT], incident_id: IncidentId
) -> ValueT | None:
    async with postgres.session() as session:
        return await session.scalar(select(column).where(IncidentRow.external_id == incident_id))


async def _set_part(
    incident_id: IncidentId, values: Mapping[str, object], *, by: str | None = None
) -> None:
    """Write one of the records kept beside the incident on its row, which must exist."""
    async with postgres.session() as session:
        updated = await session.scalar(
            update(IncidentRow)
            .where(IncidentRow.external_id == incident_id)
            .values(**values, updated_at=func.clock_timestamp(), updated_by=_person(by))
            .returning(IncidentRow.id)
        )
        if updated is None:
            raise MissingIncident(incident_id)
        await Topic.INCIDENTS.invalidate(session, key=incident_id)


class IncidentPolicies:
    """The one incident policy."""

    async def get(self) -> IncidentPolicy | None:
        async with postgres.session() as session:
            value = await session.scalar(select(IncidentPolicyRow.policy))
        return None if value is None else IncidentPolicy.model_validate(value)

    async def put(self, policy: IncidentPolicy, *, by: str | None = None) -> IncidentPolicy:
        """Save the policy; ``by`` is the GitHub login of the person saving it."""
        value = policy.model_dump(mode="json")
        person = _person(by)
        async with postgres.session() as session:
            await session.execute(
                insert(IncidentPolicyRow)
                .values(
                    id=uuid7(),
                    version=policy.version,
                    policy=value,
                    created_by=person,
                    updated_by=person,
                )
                .on_conflict_do_update(
                    index_elements=[_ONE_POLICY],
                    set_={
                        "version": policy.version,
                        "policy": value,
                        "updated_at": func.clock_timestamp(),
                        "updated_by": person,
                    },
                )
            )
            await Topic.INCIDENT_SETTINGS.invalidate(session)
        return policy


class Incidents:
    """Every enrolled channel's incident, by incident id."""

    async def get(self, id: IncidentId) -> Incident | None:
        async with postgres.session() as session:
            value = await session.scalar(
                select(IncidentRow.incident).where(IncidentRow.external_id == id)
            )
        return None if value is None else Incident.model_validate(value)

    async def put(self, id: IncidentId, record: Incident, *, by: str | None = None) -> Incident:
        """Save the incident; ``by`` is the GitHub login of the person whose action changed it."""
        person = _person(by)
        columns = {
            "slack_team_id": record.workspace_id,
            "channel_id": record.channel_id,
            "thread_id": record.thread_id,
            "status": record.status,
            "incident": record.model_dump(mode="json"),
            "updated_by": person,
        }
        async with postgres.session() as session:
            await SlackChannel.ensure(session, record.channel_id)
            await session.execute(
                insert(IncidentRow)
                .values(id=uuid7(), external_id=id, created_by=person, **columns)
                .on_conflict_do_update(
                    index_elements=[IncidentRow.external_id],
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
        query = select(IncidentRow.incident).where(IncidentRow.slack_team_id == workspace_id)
        if statuses is not None:
            query = query.where(IncidentRow.status.in_(statuses))
        async with postgres.session() as session:
            return _readable(Incident, await session.scalars(query))


class IncidentReports:
    """Each incident's latest agent report, kept on its incident row."""

    async def get(self, incident_id: IncidentId) -> IncidentReportRecord | None:
        value = await _part(IncidentRow.report, incident_id)
        return None if value is None else IncidentReportRecord.model_validate(value)

    async def put(
        self, incident_id: IncidentId, record: IncidentReportRecord
    ) -> IncidentReportRecord:
        await _set_part(incident_id, {"report": record.model_dump(mode="json")})
        return record


class IncidentHistories:
    """Curated history of every incident, kept on its incident row."""

    async def get(self, id: IncidentId) -> IncidentHistory | None:
        value = await _part(IncidentRow.history, id)
        return None if value is None else IncidentHistory.model_validate(value)

    async def put(
        self, id: IncidentId, history: IncidentHistory, *, by: str | None = None
    ) -> IncidentHistory:
        """Save the history; ``by`` is the GitHub login of the person whose action changed it."""
        await _set_part(id, {"history": history.model_dump(mode="json")}, by=by)
        return history

    async def all(self) -> list[IncidentHistory]:
        async with postgres.session() as session:
            values = await session.scalars(select(IncidentRow.history))
            return _readable(IncidentHistory, [value for value in values if value is not None])


class IncidentPostmortems:
    """Each incident's latest agent postmortem, as Markdown, kept on its incident row."""

    async def get(self, incident_id: IncidentId) -> str | None:
        return await _part(IncidentRow.postmortem, incident_id)

    async def put(self, incident_id: IncidentId, markdown: str) -> None:
        await _set_part(incident_id, {"postmortem": markdown})


class CommandReceipts:
    """Responder command receipts by claim id, each claimed before its command runs."""

    async def get(self, claim_id: str) -> CommandReceipt | None:
        async with postgres.session() as session:
            found = (
                await session.execute(
                    select(IncidentCommandRow, IncidentRow.external_id)
                    .join(IncidentRow, IncidentRow.id == IncidentCommandRow.incident)
                    .where(IncidentCommandRow.claim_id == claim_id)
                )
            ).first()
        if found is None:
            return None
        row, incident_id = found.tuple()
        return CommandReceipt(
            id=row.claim_id,
            incident_id=IncidentId(incident_id),
            action=row.action,
            content_hash=row.content_hash,
            created_at=(row.created_at or datetime.now(UTC)).isoformat(),
        )

    async def claim(self, receipt: CommandReceipt, *, by: str | None = None) -> bool:
        """Write ``receipt`` unless its claim id is taken; whether this call wrote it.

        ``by`` is the GitHub login of the responder who sent the command. Raises
        :class:`MissingIncident` when the receipt's incident has no row.
        """
        person = _person(by)
        async with postgres.session() as session:
            incident = await session.scalar(
                select(IncidentRow.id).where(IncidentRow.external_id == receipt.incident_id)
            )
            if incident is None:
                raise MissingIncident(receipt.incident_id)
            claimed = await session.scalar(
                insert(IncidentCommandRow)
                .values(
                    id=uuid7(),
                    claim_id=receipt.id,
                    incident=incident,
                    action=receipt.action,
                    content_hash=receipt.content_hash,
                    created_by=person,
                    updated_by=person,
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
