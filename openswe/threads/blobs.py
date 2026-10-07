"""Binary content deepagents offloads out of thread state, kept in PostgreSQL."""

import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from langgraph.store.base import GetOp, Item, Op, PutOp, Result, SearchOp
from sqlalchemy import delete, literal, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.agent_store import AgentStore
from openswe.database.orm import NOW, Base
from openswe.store import StoreEntry, delete_value, search_entries
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

THREAD_BLOBS_NAMESPACE = "thread_blobs"
_BLOB_REF_KEY = "deepagents_blob"
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")


class ThreadBlob(Base):
    __tablename__ = "thread_blob"

    thread_id: Mapped[str] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[JsonObject] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)


def blob_namespace(thread_id: str) -> tuple[str, str]:
    return (THREAD_BLOBS_NAMESPACE, thread_id)


class ThreadBlobs(AgentStore):
    """One thread's blobs, as the store deepagents' ``StoreBackend`` reads and writes."""

    def __init__(self, thread_id: str) -> None:
        super().__init__()
        self.thread_id = thread_id

    async def abatch(self, ops: Iterable[Op]) -> list[Result]:
        async with postgres.session() as session:
            return [await self._apply(session, op) for op in ops]

    async def _apply(self, session: AsyncSession, op: Op) -> Result:
        match op:
            case GetOp(key=key):
                blob = await session.get(ThreadBlob, (self.thread_id, key))
                return None if blob is None else self._item(blob)
            case PutOp(key=key, value=None):
                await session.execute(
                    delete(ThreadBlob).where(
                        ThreadBlob.thread_id == self.thread_id, ThreadBlob.path == key
                    )
                )
            case PutOp(key=key, value=dict() as value):
                await session.merge(ThreadBlob(thread_id=self.thread_id, path=key, value=value))
            case SearchOp(limit=limit, offset=offset):
                blobs = await session.scalars(
                    select(ThreadBlob)
                    .where(ThreadBlob.thread_id == self.thread_id)
                    .order_by(ThreadBlob.path)
                    .limit(limit)
                    .offset(offset)
                )
                return [self._item(blob) for blob in blobs]
            case _:
                raise NotImplementedError(f"ThreadBlobs does not support {type(op).__name__}")
        return None

    def _item(self, blob: ThreadBlob) -> Item:
        return Item(
            value=blob.value,
            key=blob.path,
            namespace=blob_namespace(self.thread_id),
            created_at=blob.created_at,
            updated_at=blob.created_at,
        )


def referenced_blob_digests(messages: Sequence[Any]) -> list[str]:
    """Digests of the offloaded blobs referenced by serialized ``messages``."""
    digests: dict[str, None] = {}
    for message in messages:
        content = message.get("content") if isinstance(message, Mapping) else None
        for block in content if isinstance(content, list) else []:
            ref = block.get(_BLOB_REF_KEY) if isinstance(block, Mapping) else None
            if isinstance(ref, str) and _DIGEST_RE.fullmatch(ref):
                digests[ref] = None
    return list(digests)


async def copy_thread_blobs(
    source_thread_id: str, target_thread_id: str, digests: Sequence[str]
) -> None:
    """Copy ``digests`` from one thread's blobs to another's; blobs already gone are skipped."""
    if not digests:
        return
    async with postgres.session() as session:
        await session.execute(
            insert(ThreadBlob)
            .from_select(
                ["thread_id", "path", "value"],
                select(literal(target_thread_id), ThreadBlob.path, ThreadBlob.value).where(
                    ThreadBlob.thread_id == source_thread_id,
                    ThreadBlob.path.in_([f"/{digest}" for digest in digests]),
                ),
            )
            .on_conflict_do_nothing()
        )


async def import_store_blobs() -> None:
    """Move blobs still in the LangGraph Store into PostgreSQL, a page at a time.

    Runs in the background at startup, so a large backlog never delays serving.
    """
    moved = 0
    try:
        while page := await search_entries([THREAD_BLOBS_NAMESPACE]):
            for entry in page:
                await _import_blob(entry)
            moved += len(page)
    except Exception:
        logger.exception("Importing thread blobs from the LangGraph Store failed")
    logger.info("Thread blobs imported from the LangGraph Store", extra={"moved_blobs": moved})


async def _import_blob(entry: StoreEntry) -> None:
    namespace = entry.namespace or [THREAD_BLOBS_NAMESPACE]
    if len(namespace) != 2:
        raise ValueError(f"unexpected thread blob namespace {namespace!r}")
    async with postgres.session() as session:
        await session.execute(
            insert(ThreadBlob)
            .values(thread_id=namespace[1], path=entry.key, value=entry.value)
            .on_conflict_do_nothing()
        )
    await delete_value(namespace, entry.key)
