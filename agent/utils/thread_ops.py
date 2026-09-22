"""Shared LangGraph thread helpers for the dashboard.

The webhook triggers (Slack / Linear / GitHub) dispatch through
``agent.dispatch.dispatch_agent_run`` with ``multitask_strategy="interrupt"``,
so they no longer need a busy-check or an in-process lock. The store-queue
below is retained for the dashboard's deliberate "inject a follow-up into a
run that's already in flight" path (``threads.api.send_dashboard_message``).
"""

import logging
from typing import Any

from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.schema import Thread, ThreadSelectField

from agent.config import ENV

logger = logging.getLogger(__name__)

MAX_QUEUED_MESSAGES = 100


def langgraph_url() -> str:
    return ENV.LANGGRAPH_URL.get()


def langgraph_client() -> LangGraphClient:
    return get_client(url=langgraph_url())


async def read_thread_fields(
    client: LangGraphClient, thread_id: str, fields: list[ThreadSelectField]
) -> Thread:
    """Read a thread without loading its checkpoint values."""
    threads = await client.threads.search(ids=[thread_id], select=fields, limit=1)
    if threads:
        return threads[0]
    # Preserve the single-thread endpoint's missing/inaccessible-thread errors.
    return await client.threads.get(thread_id)


async def get_thread_active_status(thread_id: str) -> bool | None:
    """Return whether the thread is active, or None when status cannot be determined."""
    try:
        thread = await read_thread_fields(langgraph_client(), thread_id, ["status"])
        status = thread.get("status", "idle")
        logger.info(
            "Checked thread status", extra={"agent_thread_id": thread_id, "thread_status": status}
        )
        return status == "busy"
    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to get thread status", extra={"agent_thread_id": thread_id}, exc_info=True
        )
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
        key = "pending_messages"
        new_message = {"content": message_content}

        existing_messages: list[dict[str, Any]] = []
        try:
            existing_item = await client.store.get_item(namespace, key)
            if existing_item and existing_item.get("value"):
                existing_messages = existing_item["value"].get("messages", [])
        except Exception:  # noqa: BLE001
            logger.debug("No existing queued messages for thread %s", thread_id)

        queue_id = message_content.get("queue_id") if isinstance(message_content, dict) else None
        if isinstance(queue_id, str) and any(
            isinstance(existing.get("content"), dict)
            and existing["content"].get("queue_id") == queue_id
            for existing in existing_messages
        ):
            return True

        existing_messages.append(new_message)
        if len(existing_messages) > MAX_QUEUED_MESSAGES:
            existing_messages = existing_messages[-MAX_QUEUED_MESSAGES:]
            logger.warning(
                "Thread %s queue capped at %d messages (dropped oldest)",
                thread_id,
                MAX_QUEUED_MESSAGES,
            )
        await client.store.put_item(namespace, key, {"messages": existing_messages})
        from agent.thread_feedback import note_feedback_activity

        await note_feedback_activity(thread_id, client=client)
        logger.info(
            "Queued message for thread %s (total queued: %d)",
            thread_id,
            len(existing_messages),
        )
        return True
    except Exception:
        logger.exception("Failed to queue message for thread %s", thread_id)
        return False
