"""Per-person JSON records in PostgreSQL, one per person, kind, and key.

Callers pass the GitHub login they have; it is resolved to ``users.id`` here,
because logins are mutable. Writing a record for a login with no ``users`` row
raises :class:`UnknownUser`: people come into being when they sign in.
"""

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, ForeignKey, ScalarSelect, and_, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.database.store_imports import StoreImport
from openswe.store import Namespace, delete_value, search_all_entries
from openswe.users.models import UserIdentity
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)


class UnknownUser(ValueError):
    """No Open SWE user signed in with this GitHub login."""

    def __init__(self, login: str) -> None:
        super().__init__(f"No Open SWE user record for {login!r} yet; sign in first")


class UserRecord(Base):
    __tablename__ = "user_record"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[JsonObject] = mapped_column(JSONB)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


def user_id_for_login(login: str) -> ScalarSelect[UUID]:
    return (
        select(UserIdentity.user_id)
        .where(
            UserIdentity.provider == "github",
            func.lower(UserIdentity.login) == login.strip().lower(),
        )
        .order_by(UserIdentity.last_seen_at.desc())
        .limit(1)
        .scalar_subquery()
    )


class UserRecords:
    """Every person's records of one ``kind``."""

    def __init__(self, kind: str) -> None:
        self.kind = kind

    def _where(self, login: str, key: str) -> ColumnElement[bool]:
        return and_(
            UserRecord.user_id == user_id_for_login(login),
            UserRecord.kind == self.kind,
            UserRecord.key == key,
        )

    async def get(self, login: str, key: str = "") -> JsonObject | None:
        async with postgres.session() as session:
            return await session.scalar(select(UserRecord.value).where(self._where(login, key)))

    async def list(self, login: str) -> dict[str, JsonObject]:
        """Every record of this kind for one person, by key."""
        async with postgres.session() as session:
            rows = await session.execute(
                select(UserRecord.key, UserRecord.value).where(
                    UserRecord.user_id == user_id_for_login(login),
                    UserRecord.kind == self.kind,
                )
            )
            return dict(rows.tuples().all())

    async def put(self, login: str, value: JsonObject, key: str = "") -> None:
        async with postgres.session() as session:
            user_id = await session.scalar(select(user_id_for_login(login)))
            if user_id is None:
                raise UnknownUser(login)
            upsert = insert(UserRecord).values(
                user_id=user_id, kind=self.kind, key=key, value=value
            )
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[UserRecord.user_id, UserRecord.kind, UserRecord.key],
                    set_={"value": value, "updated_at": func.clock_timestamp()},
                )
            )

    async def delete(self, login: str, key: str = "") -> None:
        async with postgres.session() as session:
            await session.execute(delete(UserRecord).where(self._where(login, key)))

    async def pop(self, login: str, key: str = "") -> JsonObject | None:
        """Delete a record and return what it held."""
        async with postgres.session() as session:
            return await session.scalar(
                delete(UserRecord).where(self._where(login, key)).returning(UserRecord.value)
            )

    async def import_store(self, namespace: Namespace) -> StoreImport:
        """Move Store records, keyed by login, into this kind.

        A record already in PostgreSQL wins, and one whose login has no ``users``
        row stays in the Store for the next startup.
        """
        moved = waiting = 0
        for entry in await search_all_entries(namespace):
            item_namespace = entry.namespace or list(namespace)
            if len(item_namespace) != len(namespace):
                continue
            login = entry.key
            try:
                if await self.get(login) is None:
                    await self.put(login, entry.value)
            except UnknownUser:
                logger.warning(
                    "Store record waits for its users row",
                    extra={"record_kind": self.kind, "github_login": login},
                )
                waiting += 1
                continue
            await delete_value(item_namespace, entry.key)
            moved += 1
        return StoreImport(moved, waiting)
