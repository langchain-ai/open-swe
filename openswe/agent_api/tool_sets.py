"""Every tool an MDA run started with, kept by content for the run tokens that name it.

A run's context names its tools and carries none of their specs: the Agent Server
copies the context into every checkpoint and every read of the run, and the specs of
hundreds of integrations come to a megabyte. The backend keeps every tool here as it
starts the run, once per distinct set, and signs the set's digest into the run's
token (``RunSettings.tools``). The run reads the specs of those its catalog names,
and ``search_tools`` and code mode reach the rest, through ``runtime_api.tool_search``.
"""

import hashlib
import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import Final, Self
from uuid import UUID, uuid7

from pydantic import BaseModel, JsonValue, TypeAdapter
from runtime_api.catalog import SEARCH_TOOLS, ToolSpec
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
_MAX_RESULTS: Final = 20
_WORD: Final = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
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


def _tokens(text: str) -> list[str]:
    return [word.casefold() for word in _WORD.findall(text)]


def _document(tool: ToolSpec) -> list[str]:
    properties = tool.parameters.get("properties")
    parameters = " ".join(properties) if isinstance(properties, dict) else ""
    # A match on the name says more than one buried in a long description.
    return [
        *_tokens(tool.name) * 3,
        *_tokens(tool.description),
        *_tokens(parameters),
    ]


def bm25(
    query: Sequence[str], documents: Sequence[Sequence[str]], k1: float = 1.2, b: float = 0.75
) -> list[float]:
    """Okapi BM25 score of each document for ``query``."""
    if not documents:
        return []
    average = sum(map(len, documents)) / len(documents) or 1
    counts = [Counter(document) for document in documents]
    scores = [0.0] * len(documents)
    for term in set(query):
        having = sum(1 for count in counts if term in count)
        if not having:
            continue
        idf = math.log(1 + (len(documents) - having + 0.5) / (having + 0.5))
        for index, count in enumerate(counts):
            frequency = count[term]
            if frequency:
                length = len(documents[index]) / average
                scores[index] += (
                    idf * frequency * (k1 + 1) / (frequency + k1 * (1 - b + b * length))
                )
    return scores


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

    @property
    def _searchable(self) -> list[ToolSpec]:
        # What the graph excludes the model calls neither from code nor after a search.
        excluded = set(self.excluded)
        return [tool for tool in self.tools if tool.name not in excluded]

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
        return cls(_specs.validate_python(row.tools), row.excluded)

    def specs(self, names: Iterable[str]) -> list[ToolSpec]:
        """The tools by ``names``, as a run's catalog names them."""
        wanted = set(names)
        return [tool for tool in self.tools if tool.name in wanted]

    def search(self, query: str, limit: int) -> list[ToolSpec]:
        """The tools that best match ``query``, ranked by BM25 over name, description and parameters."""
        candidates = [tool for tool in self._searchable if tool.name != SEARCH_TOOLS]
        scores = bm25(_tokens(query), [_document(tool) for tool in candidates])
        best = sorted(
            (pair for pair in zip(candidates, scores, strict=True) if pair[1] > 0),
            key=lambda pair: -pair[1],
        )
        return [tool for tool, _ in best[: max(1, min(limit, _MAX_RESULTS))]]

    def resolve(self, name: str) -> ToolSpec | None:
        """The tool a run calls as ``name``: its own name, or ``tools.<name>`` from code mode."""
        return next(
            (tool for tool in self._searchable if name in {tool.name, tool.code_name}),
            None,
        )


async def purge_tool_sets() -> int:
    """Forget the tool sets no run has started with for longer than any run token lives."""
    cls = KeptToolSet
    async with postgres.transaction() as conn:
        result = await conn.execute(delete(cls).where(cls.updated_at < func.now() - RETAINED))
    return result.rowcount
