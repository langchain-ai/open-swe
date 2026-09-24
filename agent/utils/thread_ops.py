"""Shared LangGraph thread helpers for the dashboard.

The webhook triggers (Slack / Linear / GitHub) dispatch through
``agent.dispatch.dispatch_agent_run`` with ``multitask_strategy="interrupt"``,
so they no longer need a busy-check or an in-process lock. The store-queue
below is retained for the dashboard's deliberate "inject a follow-up into a
run that's already in flight" path (``threads.api.send_dashboard_message``).
"""

import logging
from collections.abc import Mapping
from typing import Any

from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.schema import Thread
from pydantic import BaseModel

from agent.config import ENV
from agent.utils.json_types import ThreadLike, as_thread_dict, thread_metadata

logger = logging.getLogger(__name__)

MAX_QUEUED_MESSAGES = 100


def langgraph_url() -> str:
    return ENV.LANGGRAPH_URL.get()


def langgraph_client():
    return get_client(url=langgraph_url())


async def update_thread_metadata(
    thread_id: str,
    metadata: Mapping[str, object],
    *,
    client: LangGraphClient | None = None,
    current: ThreadLike | None = None,
) -> ThreadLike:
    """Merge ``metadata`` into a LangGraph thread and keep its read models in step.

    Returns the merged thread. LangGraph's response is authoritative, so the
    index row is derived from it rather than from what the caller assumed.
    Concurrent updates can still land out of order, but each one indexes the
    thread as LangGraph merged it at that write, and the reconciler re-reads
    LangGraph within a tick, so a stale row never outlives the next sweep.

    With ``current``, the caller's copy of the thread, LangGraph is asked not to
    send the thread back and the merge happens locally instead: cheaper on a hot
    read path, at the cost of indexing from that copy.

    The mirror and index writes never fail the caller: the LangGraph write that
    matters has already succeeded, and both log what they swallow.
    """
    # Local imports: both modules import this one back through
    # ``agent.threads.summary`` -> ``agent.review.session``.
    from agent.threads.index import try_upsert_thread_index
    from agent.transcript.mirror import mirror_thread_metadata

    resolved = client or langgraph_client()
    thread: ThreadLike
    if current is None:
        updated: Thread = await resolved.threads.update(thread_id=thread_id, metadata=metadata)
        thread = updated
    else:
        await resolved.threads.update(thread_id=thread_id, metadata=metadata, return_minimal=True)
        thread = {
            **as_thread_dict(current),
            "metadata": {**thread_metadata(current), **metadata},
        }
    await mirror_thread_metadata(thread_id, metadata)
    await try_upsert_thread_index(thread)
    return thread


class ThreadRunError(BaseModel):
    """The exception LangGraph records on a thread whose latest run failed."""

    error: str = ""
    message: str = ""

    def describe(self) -> str:
        return ": ".join(part for part in (self.error, self.message) if part)


class _ErroredThread(BaseModel):
    error: ThreadRunError | None = None


async def thread_run_error(thread_id: str, client: LangGraphClient | None = None) -> str | None:
    """Why the thread's latest run failed, or ``None`` when it recorded no error."""
    thread = await (client or langgraph_client()).threads.get(thread_id)
    failure = _ErroredThread.model_validate(thread).error
    return (failure.describe() or None) if failure else None


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
