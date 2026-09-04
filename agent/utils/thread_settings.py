"""Per-thread snapshot of the profile settings a conversation runs under.

Threads are multi-party and long-lived: anyone can reply, and any participant can
edit their dashboard profile at any time. Thread-level model and repository
settings are therefore resolved once on the first run and stored on the thread.
Sender identity, personal instructions, and PR preferences remain per-message
context.

Later changes reach a thread only when something explicitly rewrites the
snapshot, which today means a per-run model override.
"""

import logging
from collections.abc import Mapping
from typing import Any, TypedDict

from pydantic import TypeAdapter, ValidationError

from agent.utils import ttl_cache

logger = logging.getLogger(__name__)

THREAD_SETTINGS_KEY = "agent_settings"
_CACHE_TTL_SECONDS = 300


class ThreadSettings(TypedDict, total=False):
    model_id: str
    effort: str | None
    subagent_model_id: str
    subagent_effort: str | None
    adaptive_model_routing: bool
    repo_instructions: str | None


_THREAD_SETTINGS_ADAPTER = TypeAdapter(ThreadSettings)


def normalize_thread_settings(settings: Mapping[str, Any]) -> tuple[ThreadSettings, bool]:
    """Remove obsolete or invalid settings from stored thread metadata."""
    try:
        value = _THREAD_SETTINGS_ADAPTER.validate_python(settings, strict=True)
    except ValidationError:
        return {}, bool(settings)
    return value, value != settings


def _cache_key(thread_id: str) -> str:
    return f"thread-settings:{thread_id}"


async def load_thread_settings(client: Any, thread_id: str) -> ThreadSettings:
    """The thread's stored settings, or an empty mapping when it has none yet."""

    async def _load() -> ThreadSettings:
        thread = await client.threads.get(thread_id=thread_id)
        metadata = thread.get("metadata") or {}
        stored = metadata.get(THREAD_SETTINGS_KEY)
        return normalize_thread_settings(stored)[0] if isinstance(stored, dict) else {}

    try:
        return await ttl_cache.cached(_cache_key(thread_id), _CACHE_TTL_SECONDS, _load)
    except Exception:
        logger.debug("Could not read settings for thread %s", thread_id, exc_info=True)
        return {}


async def store_thread_settings(client: Any, thread_id: str, settings: ThreadSettings) -> None:
    """Persist the thread's settings, replacing any previous snapshot."""
    try:
        await client.threads.update(
            thread_id=thread_id, metadata={THREAD_SETTINGS_KEY: dict(settings)}
        )
    except Exception:
        logger.debug("Could not store settings for thread %s", thread_id, exc_info=True)
        return
    ttl_cache.set_cached(_cache_key(thread_id), settings, _CACHE_TTL_SECONDS)
