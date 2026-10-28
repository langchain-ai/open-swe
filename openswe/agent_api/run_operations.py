"""Every operation an MDA run started with, kept by content for the run tokens that name it.

A run's catalog leaves its integrations out: hundreds of them would put a megabyte
in each run's context, which the Agent Server copies into every checkpoint and every
read of the run. The backend keeps every operation here as it starts the run, once
per distinct set, and signs their digest into the run's token
(``RunSettings.operations``); the run's ``search_tools`` and code mode reach them
through ``runtime_api.tool_search``.
"""

import hashlib
import math
import re
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final, Self
from uuid import UUID, uuid7

from pydantic import JsonValue, TypeAdapter
from runtime_api.catalog import SEARCH_TOOLS, OperationSpec
from sqlalchemy import ForeignKey, delete, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.users.models import User

# Past the life of any run token, so no run outlives the operations it names.
RETAINED: Final = timedelta(days=3)
# How stale a kept set's last start may be before a run starting with it marks it
# again; marking every start would write the same row for each run.
_MARKED_WITHIN: Final = timedelta(hours=1)
PURGE_JOB: Final = "run_operations_purge"
PURGE_SCHEDULE: Final = "41 * * * *"
_MAX_RESULTS: Final = 20
_WORD: Final = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
_operations = TypeAdapter(list[OperationSpec])


class KeptOperations(Base):
    __tablename__ = "run_operations"

    digest: Mapped[str]
    operations: Mapped[list[JsonValue]] = mapped_column(JSONB)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )


class OperationsNotKeptError(LookupError):
    """No operations are kept under the digest a run's token names."""


def _tokens(text: str) -> list[str]:
    return [word.casefold() for word in _WORD.findall(text)]


def _document(operation: OperationSpec) -> list[str]:
    properties = operation.parameters.get("properties")
    parameters = " ".join(properties) if isinstance(properties, dict) else ""
    # A match on the name says more than one buried in a long description.
    return [
        *_tokens(operation.name) * 3,
        *_tokens(operation.description),
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


class RunOperations:
    """Every operation a run may call, without the run's grants."""

    def __init__(self, operations: Sequence[OperationSpec]) -> None:
        self.operations = sorted(
            (operation.model_copy(update={"grant": None}) for operation in operations),
            key=lambda operation: operation.name,
        )

    def _canonical(self) -> bytes:
        return _operations.dump_json(self.operations)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self._canonical()).hexdigest()

    async def keep(self) -> str:
        """Keep these operations, and return the digest a run's token names them by.

        Most runs start with a set already kept, which costs only marking it as still
        in use; only a new set is sent to the database.
        """
        digest = self.digest
        cls = KeptOperations
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
                        operations=_operations.dump_python(self.operations, mode="json"),
                    )
                    .on_conflict_do_nothing(index_elements=[cls.digest])
                )
        return digest

    @classmethod
    async def kept(cls, digest: str) -> Self:
        async with postgres.session() as session:
            operations = await session.scalar(
                select(KeptOperations.operations).where(KeptOperations.digest == digest)
            )
        if operations is None:
            raise OperationsNotKeptError(digest)
        return cls(_operations.validate_python(operations))

    def search(self, query: str, limit: int) -> list[OperationSpec]:
        """The operations that best match ``query``, ranked by BM25 over name, description and parameters."""
        candidates = [operation for operation in self.operations if operation.name != SEARCH_TOOLS]
        scores = bm25(_tokens(query), [_document(operation) for operation in candidates])
        best = sorted(
            (pair for pair in zip(candidates, scores, strict=True) if pair[1] > 0),
            key=lambda pair: -pair[1],
        )
        return [operation for operation, _ in best[: max(1, min(limit, _MAX_RESULTS))]]

    def called(self, name: str) -> OperationSpec | None:
        """The operation a run calls as ``name``: its own name, or ``tools.<name>`` from code mode."""
        return next(
            (
                operation
                for operation in self.operations
                if name in {operation.name, operation.code_name}
            ),
            None,
        )


async def purge_run_operations() -> int:
    """Forget the operations no run has started with for longer than any run token lives."""
    cls = KeptOperations
    async with postgres.transaction() as conn:
        result = await conn.execute(delete(cls).where(cls.updated_at < func.now() - RETAINED))
    return result.rowcount
