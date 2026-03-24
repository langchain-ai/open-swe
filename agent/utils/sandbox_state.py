"""Shared sandbox state used by server and middleware."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langgraph.config import get_config

from .sandbox import create_sandbox

logger = logging.getLogger(__name__)

# Thread ID -> SandboxBackend mapping, shared between server.py and middleware
SANDBOX_BACKENDS: dict[str, Any] = {}


async def get_sandbox_id_from_metadata(thread_id: str) -> str | None:
    """Fetch sandbox_id from thread metadata."""
    try:
        config = get_config()
    except Exception:
        logger.exception("Failed to read thread metadata for sandbox")
        return None
    return config.get("metadata", {}).get("sandbox_id")


async def get_sandbox_backend(thread_id: str) -> Any | None:
    """Get sandbox backend from cache, or connect using thread metadata."""
    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    if sandbox_backend:
        return sandbox_backend

    sandbox_id = await get_sandbox_id_from_metadata(thread_id)
    if not sandbox_id:
        raise ValueError(f"Missing sandbox_id in thread metadata for {thread_id}")

    sandbox_backend = await asyncio.to_thread(create_sandbox, sandbox_id)
    SANDBOX_BACKENDS[thread_id] = sandbox_backend
    return sandbox_backend
