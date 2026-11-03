"""One read of a thread, shared by the lookups a run makes while it starts up.

A run's factory and its prepare step each consult the same thread several times
(credential scope, task coordination, tool access, participants, token scope,
the sandbox's tool proxy, ...), and every read is a round trip through the
thread API. The share lasts one request, never longer.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

import langgraph_sdk
from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.schema import Thread

_SHARED: ContextVar[dict[str, asyncio.Future[Thread]] | None] = ContextVar(
    "shared_thread_reads", default=None
)


@asynccontextmanager
async def shared_thread_reads() -> AsyncIterator[None]:
    """Let ``read_thread`` calls in this block share one read per thread.

    Only lookups of fields the block does not itself write (ownership,
    visibility, participants) use it; anything that needs the live record keeps
    reading through the client. A block inside another shares the outer one's reads.
    """
    if _SHARED.get() is not None:
        yield
        return
    token = _SHARED.set({})
    try:
        yield
    finally:
        _SHARED.reset(token)


async def read_thread(thread_id: str, client: LangGraphClient | None = None) -> Thread:
    client = client or langgraph_sdk.get_client()
    shared = _SHARED.get()
    if shared is None:
        return await client.threads.get(thread_id)
    read = shared.get(thread_id)
    if read is None:
        read = shared[thread_id] = asyncio.ensure_future(client.threads.get(thread_id))
    try:
        return await asyncio.shield(read)
    except Exception:
        # A failed read is not shared: the next lookup tries again, as before.
        if shared.get(thread_id) is read:
            del shared[thread_id]
        raise
