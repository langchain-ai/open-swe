"""Every tool an MDA run started with, kept by content for the run tokens that name it.

A run's context names its tools and carries none of their specs: the Agent Server
copies the context into every checkpoint and every read of the run, and the specs of
hundreds of integrations come to a megabyte. The backend keeps every tool here as it
starts the run, once per distinct set, and signs the set's digest into the run's
token (``RunSettings.tools``). The run reads the specs of those its catalog names,
and ``search_tools`` and code mode reach the rest, through ``runtime_api.tool_search``.
A set never changes under its digest, so each process reads it once.
"""

import hashlib
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from functools import cached_property
from typing import Final, Self, cast
from uuid import UUID, uuid7

from pydantic import BaseModel, JsonValue, TypeAdapter
from runtime_api.catalog import ToolSpec
from runtime_api.tool_search import ToolIndex
from sqlalchemy import ForeignKey, delete, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.users.models import User

# Past the life of any run token, so no run outlives the tools it names.
RETAINED: Final = timedelta(days=3)
# How stale a kept set's last start may be before a run starting with it marks it
# again; marking every start would write the same row for each run.
_MARKED_WITHIN: Final = timedelta(hours=1)
PURGE_JOB: Final = "tool_sets_purge"
PURGE_SCHEDULE: Final = "41 * * * *"
# How many sets a process keeps read: a set never changes under its digest, so each
# run's searches and lookups read it once, and a set may come to a megabyte.
_READ_LIMIT: Final = 32
_specs = TypeAdapter(list[ToolSpec])


class KeptToolSet(Base):
    __tablename__ = "run_tool_sets"

    digest: Mapped[str]
    tools: Mapped[list[JsonValue]] = mapped_column(JSONB)
    # Those the graph keeps from the model: read by a run that names them, never searched.
    excluded: Mapped[list[str]] = mapped_column(JSONB)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )


class _Digested(BaseModel):
    """What a digest is taken over."""

    tools: list[ToolSpec]
    excluded: list[str]


class ToolSetNotKeptError(LookupError):
    """No tool set is kept under the digest a run's token names."""


class ToolSet:
    """Every tool a run may call, without the run's grants, and those the graph excludes."""

    def __init__(self, tools: Sequence[ToolSpec], excluded: Iterable[str] = ()) -> None:
        self.tools = sorted(
            (tool.model_copy(update={"grant": None}) for tool in tools),
            key=lambda tool: tool.name,
        )
        self.excluded = sorted(set(excluded))

    def _canonical(self) -> bytes:
        return _Digested(tools=self.tools, excluded=self.excluded).model_dump_json().encode()

    @cached_property
    def _index(self) -> ToolIndex:
        return ToolIndex(self.tools, self.excluded)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self._canonical()).hexdigest()

    async def keep(self) -> str:
        """Keep this set, and return the digest a run's token names it by.

        Most runs start with a set already kept, which costs only marking it as still
        in use; only a new set is sent to the database.
        """
        digest = self.digest
        cls = KeptToolSet
        async with postgres.transaction() as conn:
            marked = await conn.execute(
                update(cls)
                .where(cls.digest == digest, cls.updated_at < func.now() - _MARKED_WITHIN)
                .values(updated_at=NOW)
            )
            kept = marked.rowcount > 0 or await conn.scalar(
                select(cls.id).where(cls.digest == digest)
            )
            if not kept:
                await conn.execute(
                    insert(cls)
                    .values(
                        id=uuid7(),
                        digest=digest,
                        tools=_specs.dump_python(self.tools, mode="json"),
                        excluded=self.excluded,
                    )
                    .on_conflict_do_nothing(index_elements=[cls.digest])
                )
        return digest

    @classmethod
    async def kept(cls, digest: str) -> Self:
        """The set kept under ``digest``, read from PostgreSQL once per process."""
        read = _read.get(digest)
        if read is not None:
            _read.move_to_end(digest)
            return cast(Self, read)
        async with postgres.session() as session:
            row = (
                await session.execute(
                    select(KeptToolSet.tools, KeptToolSet.excluded).where(
                        KeptToolSet.digest == digest
                    )
                )
            ).one_or_none()
        if row is None:
            raise ToolSetNotKeptError(digest)
        kept = cls(_specs.validate_python(row.tools), row.excluded)
        _read[digest] = kept
        while len(_read) > _READ_LIMIT:
            _read.popitem(last=False)
        return kept

    def specs(self, names: Iterable[str]) -> list[ToolSpec]:
        """The tools by ``names``, as a run's catalog names them."""
        wanted = set(names)
        return [tool for tool in self.tools if tool.name in wanted]

    def search(self, query: str) -> list[ToolSpec]:
        """The tools that best match ``query``, as ``ToolIndex.search`` ranks them."""
        return self._index.search(query)

    def resolve(self, name: str) -> ToolSpec | None:
        """The tool a run calls as ``name``: its own name, or ``tools.<name>`` from code mode."""
        return self._index.resolve(name)


# The sets this process read, least recently used first.
_read: OrderedDict[str, ToolSet] = OrderedDict()


async def purge_tool_sets() -> int:
    """Forget the tool sets no run has started with for longer than any run token lives."""
    cls = KeptToolSet
    async with postgres.transaction() as conn:
        result = await conn.execute(delete(cls).where(cls.updated_at < func.now() - RETAINED))
    return result.rowcount
