"""Shared LangGraph thread helpers for the dashboard.

The webhook triggers (Slack / Linear / GitHub) dispatch through
``agent.dispatch.dispatch_agent_run`` with ``multitask_strategy="interrupt"``,
so they no longer need a busy-check or an in-process lock. The store-queue
below is retained for the dashboard's deliberate "inject a follow-up into a
run that's already in flight" path (``thread_api.send_dashboard_message``).
"""

import logging
import os
from collections.abc import Mapping
from typing import Any, Self, cast

from langgraph_sdk import get_client
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.input_messages import PersonIdentity
from agent.media import MediaRef

logger = logging.getLogger(__name__)

MAX_QUEUED_MESSAGES = 100


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


class QueuedSender(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    display_name: str | None = None
    handle: str | None = None
    platform: str | None = None
    github_login: str | None = None
    email: str | None = None
    timezone: str | None = None

    def identity(self) -> PersonIdentity:
        return cast(PersonIdentity, self.model_dump(exclude_none=True))


class QueuedMessage(BaseModel):
    """A follow-up waiting in the store for a run that is already in flight.

    ``sender`` attributes the text to a person; without it the text is
    delivered as a system notice. Attachments travel as media references,
    never as bytes.
    """

    model_config = ConfigDict(extra="ignore")

    text: str = ""
    source: str | None = None
    sender: QueuedSender | None = None
    media: list[MediaRef] = Field(default_factory=list)

    @classmethod
    def parse(cls, raw: object) -> Self | None:
        if isinstance(raw, str):
            return cls(text=raw) if raw else None
        if not isinstance(raw, Mapping):
            return None
        try:
            return cls.model_validate(dict(raw))
        except ValidationError:
            logger.warning("Dropping malformed queued message", exc_info=True)
            return None

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


async def queue_message_for_thread(thread_id: str, message: QueuedMessage) -> bool:
    """Queue a follow-up message for a busy thread (FIFO store namespace).

    Used by the dashboard to inject a follow-up into a run that's already in
    flight; webhook triggers use ``multitask_strategy="interrupt"`` instead.
    """
    client = langgraph_client()
    try:
        namespace = ("queue", thread_id)
        key = "pending_messages"
        new_message = {"content": message.dump()}

        existing_messages: list[dict[str, Any]] = []
        try:
            existing_item = await client.store.get_item(namespace, key)
            if existing_item and existing_item.get("value"):
                existing_messages = existing_item["value"].get("messages", [])
        except Exception:  # noqa: BLE001
            logger.debug("No existing queued messages for thread %s", thread_id)

        existing_messages.append(new_message)
        if len(existing_messages) > MAX_QUEUED_MESSAGES:
            existing_messages = existing_messages[-MAX_QUEUED_MESSAGES:]
            logger.warning(
                "Thread %s queue capped at %d messages (dropped oldest)",
                thread_id,
                MAX_QUEUED_MESSAGES,
            )
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
