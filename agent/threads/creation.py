"""The only place LangGraph threads are created, so every thread a person can open has a title."""

import logging
from collections.abc import Mapping
from typing import Literal

from langgraph_sdk.client import LangGraphClient

from agent.threads.tools_in_sandbox import (
    PREFER_TOOLS_IN_SANDBOX_KEY,
    owner_prefers_tools_in_sandbox,
)
from agent.utils.json_types import thread_metadata

logger = logging.getLogger(__name__)

# Set when a person renames a thread, so system-generated titles stop replacing theirs.
TITLE_LOCKED_KEY = "title_locked"


def _require_title(title: str) -> str:
    if not title.strip():
        raise ValueError("a thread needs a title")
    return title


async def create_thread(
    client: LangGraphClient,
    thread_id: str,
    *,
    title: str,
    if_exists: Literal["raise", "do_nothing"],
    metadata: Mapping[str, object] | None = None,
) -> None:
    """Create a thread people can open; with ``do_nothing`` an existing thread keeps its metadata."""
    stamped = {**(metadata or {}), "title": _require_title(title)}
    stamped.pop(PREFER_TOOLS_IN_SANDBOX_KEY, None)
    owner_login = stamped.get("owner_login")
    if (
        isinstance(owner_login, str)
        and owner_login.strip()
        and await owner_prefers_tools_in_sandbox(owner_login.strip())
    ):
        stamped[PREFER_TOOLS_IN_SANDBOX_KEY] = True
    thread = await client.threads.create(thread_id=thread_id, if_exists=if_exists, metadata=stamped)
    if stamped.get(PREFER_TOOLS_IN_SANDBOX_KEY) and (
        thread_metadata(thread).get(PREFER_TOOLS_IN_SANDBOX_KEY) is True
    ):
        logger.info(
            "Thread created preferring tools in the sandbox",
            extra={"thread_id": thread_id, "owner_login": owner_login},
        )


async def ensure_titled_thread(client: LangGraphClient, thread_id: str, *, title: str) -> None:
    """Create a system-named thread if needed and keep its title current, unless someone renamed it."""
    thread = await client.threads.create(
        thread_id=thread_id, if_exists="do_nothing", metadata={"title": _require_title(title)}
    )
    metadata = thread_metadata(thread)
    if metadata.get(TITLE_LOCKED_KEY) is not True and metadata.get("title") != title:
        await client.threads.update(thread_id=thread_id, metadata={"title": title})


async def create_lock_thread(
    client: LangGraphClient,
    thread_id: str,
    *,
    ttl_minutes: int,
    metadata: Mapping[str, object] | None = None,
) -> None:
    """Hold a short-lived, never-listed thread as a mutex; raises ``ConflictError`` while held."""
    if metadata:
        await client.threads.create(
            thread_id=thread_id, if_exists="raise", ttl=ttl_minutes, metadata=dict(metadata)
        )
    else:
        await client.threads.create(thread_id=thread_id, if_exists="raise", ttl=ttl_minutes)
