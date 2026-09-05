"""Fail-closed distributed ownership using LangGraph's atomic thread creation."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from langgraph_sdk.errors import ConflictError

from agent import store


@asynccontextmanager
async def distributed_lock(key: Sequence[str], *, timeout: float = 60) -> AsyncIterator[None]:
    """Keep the ownership row on TTL sweep or failure; only clean success releases it."""
    lock_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "open-swe:ownership:" + json.dumps(list(key))))
    client = store.store_client()
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        try:
            await client.threads.create(
                thread_id=lock_id,
                if_exists="raise",
                ttl={"strategy": "keep_latest", "ttl": 60},
            )
            break
        except ConflictError:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Shared ownership is unavailable") from None
            await asyncio.sleep(0.1)
    yield
    await client.threads.delete(lock_id)
