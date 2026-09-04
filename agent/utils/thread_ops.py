"""Shared LangGraph thread helpers for the dashboard.

The webhook triggers (Slack / Linear / GitHub) dispatch through
``agent.dispatch.dispatch_agent_run`` with ``multitask_strategy="interrupt"``,
so they no longer need a busy-check or an in-process lock. The store-queue
below is retained for the dashboard's deliberate "inject a follow-up into a
run that's already in flight" path (``thread_api.send_dashboard_message``).
"""

import logging
import os
import time
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from langgraph_sdk import get_client

logger = logging.getLogger(__name__)

MAX_QUEUED_MESSAGES = 100
PENDING_MESSAGES_KEY = "pending_messages"
QUEUE_MESSAGE_KEY_PREFIX = "message:"


def langgraph_url() -> str:
    return os.environ.get("LANGGRAPH_URL") or os.environ.get(
        "LANGGRAPH_URL_PROD", "http://localhost:2024"
    )


def langgraph_client():
    return get_client(url=langgraph_url())


async def get_thread_active_status(thread_id: str) -> bool | None:
    """Return whether the thread is active, or None when status cannot be determined."""
    try:
        thread = await langgraph_client().threads.get(thread_id)
        status = thread.get("status", "idle") if isinstance(thread, dict) else "idle"
        logger.info("Thread %s status check: status=%s", thread_id, status)
        return status == "busy"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to get thread status for %s: %s", thread_id, exc)
        return None


async def queue_message_for_thread(
    thread_id: str, message_content: str | list[dict[str, Any]] | dict[str, Any]
) -> bool:
    """Queue a follow-up message for a busy thread (FIFO store namespace).

    Used by the dashboard to inject a follow-up into a run that's already in
    flight; webhook triggers use ``multitask_strategy="interrupt"`` instead.
    """
    client = langgraph_client()
    try:
        namespace = ("queue", thread_id)
        key = f"{QUEUE_MESSAGE_KEY_PREFIX}{time.time_ns():020d}-{uuid4().hex}"
        await client.store.put_item(
            namespace,
            key,
            {"content": message_content, "created_at_ns": time.time_ns()},
        )
        logger.info("Queued message for thread %s", thread_id)
        return True
    except Exception:
        logger.exception("Failed to queue message for thread %s", thread_id)
        return False


async def queued_message_keys(client: Any, thread_id: str) -> list[str]:
    """Return append-only queue record keys for a thread."""
    result = await client.store.search_items(
        ("queue", thread_id), limit=MAX_QUEUED_MESSAGES, offset=0
    )
    items = result.get("items", []) if isinstance(result, Mapping) else []
    return [
        key
        for item in items
        if isinstance(item, Mapping)
        and isinstance((key := item.get("key")), str)
        and key.startswith(QUEUE_MESSAGE_KEY_PREFIX)
    ]


async def has_queued_messages(client: Any, thread_id: str) -> bool:
    """Return whether a thread has legacy or append-only queued messages."""
    try:
        legacy = await client.store.get_item(("queue", thread_id), PENDING_MESSAGES_KEY)
    except Exception:  # noqa: BLE001
        legacy = None
    if isinstance(legacy, Mapping):
        value = legacy.get("value")
        if isinstance(value, Mapping) and value.get("messages"):
            return True
    return bool(await queued_message_keys(client, thread_id))


async def queued_message_count(client: Any, thread_id: str) -> int:
    """Return the number of legacy and append-only queued messages."""
    legacy_count = 0
    try:
        legacy = await client.store.get_item(("queue", thread_id), PENDING_MESSAGES_KEY)
    except Exception:  # noqa: BLE001
        legacy = None
    if isinstance(legacy, Mapping):
        value = legacy.get("value")
        messages = value.get("messages") if isinstance(value, Mapping) else None
        legacy_count = len(messages) if isinstance(messages, list) else 0
    return legacy_count + len(await queued_message_keys(client, thread_id))
