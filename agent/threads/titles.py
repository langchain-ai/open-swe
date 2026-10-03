"""Persist manual thread titles and serialize every title writer."""

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from weakref import WeakValueDictionary

from langgraph_sdk.client import LangGraphClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.database import postgres
from agent.transcript.mirror import mirror_thread_metadata
from agent.utils.json_types import thread_metadata

TITLE_LOCKED_KEY = "title_locked"
_local_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


@asynccontextmanager
async def _title_lock(thread_id: str) -> AsyncIterator[AsyncConnection | None]:
    async with _local_locks.setdefault(thread_id, asyncio.Lock()):
        if not postgres.configured():
            yield None
            return
        async with postgres.transaction() as conn:
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:subject, 0))"),
                {"subject": f"thread-title:{thread_id}"},
            )
            yield conn


async def update_thread_title(
    client: LangGraphClient,
    thread_id: str,
    metadata: Mapping[str, object],
    *,
    manual: bool = False,
    expected_seed: str | None = None,
) -> dict[str, object]:
    """Apply a title patch unless a person named the thread, preserving unrelated metadata."""
    if "title" not in metadata and not manual:
        patch = dict(metadata)
        await client.threads.update(thread_id=thread_id, metadata=patch)
        await mirror_thread_metadata(thread_id, patch)
        return patch
    async with _title_lock(thread_id) as conn:
        current = thread_metadata(await client.threads.get(thread_id=thread_id))
        saved_title = None
        if conn is not None:
            saved_title = (
                await conn.execute(
                    text("SELECT title FROM thread_title_override WHERE thread_id = :thread_id"),
                    {"thread_id": thread_id},
                )
            ).scalar_one_or_none()
        locked = saved_title is not None or current.get(TITLE_LOCKED_KEY) is True
        patch = dict(metadata)
        if not manual and (
            locked
            or expected_seed is not None
            and (
                current.get("title") != expected_seed or current.get("title_seed") != expected_seed
            )
        ):
            for key in ("title", "title_seed", TITLE_LOCKED_KEY):
                patch.pop(key, None)
        persist_title = None
        if manual:
            title = patch.get("title")
            if not isinstance(title, str) or not title.strip():
                raise ValueError("a thread needs a title")
            patch.update({"title_seed": None, TITLE_LOCKED_KEY: True})
            persist_title = title
        elif locked and saved_title is None:
            persist_title = current.get("title")
        if conn is not None and isinstance(persist_title, str):
            await conn.execute(
                text("""
                    INSERT INTO thread_title_override (thread_id, title)
                    VALUES (:thread_id, :title)
                    ON CONFLICT (thread_id) DO UPDATE SET title = EXCLUDED.title
                """),
                {"thread_id": thread_id, "title": persist_title},
            )
        if patch:
            await client.threads.update(thread_id=thread_id, metadata=patch)
            await mirror_thread_metadata(thread_id, patch)
        return patch
