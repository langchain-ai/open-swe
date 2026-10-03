import asyncio
from typing import cast

import pytest
from langgraph_sdk.client import LangGraphClient
from sqlalchemy import text

from agent.database import postgres
from agent.threads.creation import ensure_titled_thread
from agent.threads.titles import TITLE_LOCKED_KEY, update_thread_title


class _Threads:
    def __init__(self, metadata: dict[str, object]) -> None:
        self.metadata = metadata

    async def create(self, **kwargs: object) -> dict[str, object]:
        return {"thread_id": kwargs["thread_id"], "metadata": dict(self.metadata)}

    async def get(self, *, thread_id: str) -> dict[str, object]:
        return {"thread_id": thread_id, "metadata": dict(self.metadata)}

    async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
        self.metadata.update(metadata)


class _Client:
    def __init__(self, metadata: dict[str, object]) -> None:
        self.threads = _Threads(metadata)


async def test_system_title_refreshes_until_someone_renames_the_thread() -> None:
    client = _Client({"title": "Review: #7 Old title"})

    await ensure_titled_thread(cast(LangGraphClient, client), "t", title="Review: #7 New title")
    assert client.threads.metadata["title"] == "Review: #7 New title"

    client.threads.metadata.update({"title": "My name for it", TITLE_LOCKED_KEY: True})
    await ensure_titled_thread(cast(LangGraphClient, client), "t", title="Review: #7 Newer title")
    assert client.threads.metadata["title"] == "My name for it"


async def test_manual_title_survives_concurrent_automatic_updates(
    registry_db: None,
) -> None:
    client = _Client({"title": "Original", "title_seed": "Original"})
    sdk = cast(LangGraphClient, client)
    await asyncio.gather(
        update_thread_title(sdk, "t", {"title": "Generated", "title_seed": None}),
        update_thread_title(sdk, "t", {"title": "My title"}, manual=True),
        update_thread_title(sdk, "t", {"title": "PR title", "pr_number": 7}),
    )
    assert client.threads.metadata["title"] == "My title"
    assert client.threads.metadata[TITLE_LOCKED_KEY] is True
    assert client.threads.metadata["pr_number"] == 7
    async with postgres.connection() as conn:
        saved = await conn.execute(
            text("SELECT title FROM thread_title_override WHERE thread_id = 't'")
        )
        assert saved.scalar_one() == "My title"

    client.threads.metadata.pop(TITLE_LOCKED_KEY)
    await update_thread_title(sdk, "t", {"title": "Another PR", "pr_number": 8})
    assert client.threads.metadata["title"] == "My title"
    assert client.threads.metadata["pr_number"] == 8
    await update_thread_title(sdk, "t", {"title": "My newer title"}, manual=True)
    await ensure_titled_thread(sdk, "t", title="A reviewer title")
    assert client.threads.metadata["title"] == "My newer title"


async def test_failed_manual_rename_does_not_persist_override(registry_db: None) -> None:
    class UnavailableThreads(_Threads):
        async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
            raise OSError("Thread storage unavailable")

    client = _Client({"title": "Original"})
    client.threads = UnavailableThreads(client.threads.metadata)
    with pytest.raises(OSError, match="Thread storage unavailable"):
        await update_thread_title(
            cast(LangGraphClient, client), "t", {"title": "My title"}, manual=True
        )
    assert client.threads.metadata["title"] == "Original"
    async with postgres.connection() as conn:
        saved = await conn.execute(
            text("SELECT title FROM thread_title_override WHERE thread_id = 't'")
        )
        assert saved.scalar_one_or_none() is None
