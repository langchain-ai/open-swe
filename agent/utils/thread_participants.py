"""Who is party to a thread, for tools that act on a named person's behalf.

Per-user integrations used to be bound to whoever triggered a run, which made
the tool list — and therefore the cached prompt prefix — change every time a
different person replied. The tools are now declared once and take the
participant to act for as an argument, validated against this roster.
"""

from __future__ import annotations

import logging

from langgraph.config import get_config

from . import ttl_cache
from .json_types import as_json_object

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60


def current_thread_id() -> str | None:
    configurable = as_json_object(get_config()).get("configurable")
    if not isinstance(configurable, dict):
        return None
    thread_id = configurable.get("thread_id")
    return thread_id if isinstance(thread_id, str) and thread_id else None


async def thread_participants(client: object, thread_id: str) -> list[str]:
    """Logins that have spoken in this thread, owner first."""

    async def _load() -> list[str]:
        thread = await client.threads.get(thread_id=thread_id)  # type: ignore[attr-defined]
        metadata = thread.get("metadata") or {}
        logins: list[str] = []
        owner = metadata.get("github_login")
        if isinstance(owner, str) and owner.strip():
            logins.append(owner.strip())
        recorded = metadata.get("participant_logins")
        if isinstance(recorded, list):
            logins.extend(
                entry.strip()
                for entry in recorded
                if isinstance(entry, str) and entry.strip() and entry.strip() not in logins
            )
        return logins

    try:
        return await ttl_cache.cached(f"thread-participants:{thread_id}", _CACHE_TTL_SECONDS, _load)
    except Exception:
        logger.debug("Could not read participants for thread %s", thread_id, exc_info=True)
        return []


async def resolve_participant(on_behalf_of: str) -> str:
    """Validate ``on_behalf_of`` against the current thread, returning the login.

    Raises ``ValueError`` with a message the agent can act on when the login is
    not part of the thread.
    """
    login = (on_behalf_of or "").strip()
    if not login:
        raise ValueError("on_behalf_of is required: name the thread participant to act for.")
    thread_id = current_thread_id()
    if thread_id is None:
        return login
    from langgraph_sdk import get_client

    participants = await thread_participants(get_client(), thread_id)
    if not participants:
        return login
    if login not in participants:
        raise ValueError(
            f"{login!r} is not a participant in this thread. "
            f"Participants: {', '.join(participants)}."
        )
    return login
