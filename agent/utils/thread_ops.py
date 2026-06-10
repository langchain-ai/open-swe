"""Shared LangGraph thread helpers for webhooks and the dashboard."""

from __future__ import annotations

import logging
import os
from typing import Any

from langgraph_sdk import get_client

logger = logging.getLogger(__name__)


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


async def is_thread_active(thread_id: str) -> bool:
    """Return whether the thread currently has a running run."""
    return await get_thread_active_status(thread_id) is True


async def queue_message_for_thread(
    thread_id: str, message_content: str | list[dict[str, Any]] | dict[str, Any]
) -> bool:
    """Queue a follow-up message for a busy thread (FIFO store namespace)."""
    client = langgraph_client()
    try:
        namespace = ("queue", thread_id)
        key = "pending_messages"
        new_message = {"content": message_content}

        existing_messages: list[dict[str, Any]] = []
        try:
            existing_item = await client.store.get_item(namespace, key)
            if existing_item and existing_item.get("value"):
                existing_messages = existing_item["value"].get("messages", [])
        except Exception:  # noqa: BLE001
            logger.debug("No existing queued messages for thread %s", thread_id)

        existing_messages.append(new_message)
        await client.store.put_item(namespace, key, {"messages": existing_messages})
        logger.info(
            "Queued message for thread %s (total queued: %d)",
            thread_id,
            len(existing_messages),
        )
        return True
    except Exception:
        logger.exception("Failed to queue message for thread %s", thread_id)
        return False
