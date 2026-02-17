"""Shared sandbox state used by server and middleware."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langgraph_sdk import get_client

from ..integrations.langsmith import _create_langsmith_sandbox

logger = logging.getLogger(__name__)
client = get_client()

# Thread ID -> SandboxBackend mapping, shared between server.py and middleware
SANDBOX_BACKENDS: dict[str, Any] = {}


async def _get_sandbox_id_from_metadata(thread_id: str) -> str | None:
    """Fetch sandbox_id from thread metadata."""
    try:
        thread = await client.threads.get(thread_id=thread_id)
    except Exception:
        logger.exception("Failed to fetch thread metadata for sandbox")
        return None
    return thread.get("metadata", {}).get("sandbox_id")


async def get_sandbox_backend(thread_id: str) -> Any | None:
    """Get sandbox backend from cache, or connect using thread metadata."""
    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    if sandbox_backend:
        return sandbox_backend

    sandbox_id = await _get_sandbox_id_from_metadata(thread_id)
    if not sandbox_id:
        return None

    sandbox_backend = await asyncio.to_thread(_create_langsmith_sandbox, sandbox_id)
    SANDBOX_BACKENDS[thread_id] = sandbox_backend
    return sandbox_backend


def get_sandbox_backend_sync(thread_id: str) -> Any | None:
    """Sync wrapper for get_sandbox_backend."""
    return asyncio.run(get_sandbox_backend(thread_id))
