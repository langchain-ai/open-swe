"""The incident channel policy in PostgreSQL."""

from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import ForeignKey, ScalarSelect, func, select, text
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.incidents.models import IncidentPolicy
from openswe.ui_invalidations import Topic
from openswe.users.records import user_id_for_login
from openswe.utils.json_types import JsonObject

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


def _person(login: str | None) -> ScalarSelect[UUID] | None:
    """The ``users.id`` of the person with GitHub ``login``; ``None`` for a system write."""
    return user_id_for_login(login) if login else None


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
