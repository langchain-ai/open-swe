"""Before-model middleware that injects queued messages into state.

Checks the LangGraph store for pending messages (e.g. follow-up Linear
comments that arrived while the agent was busy) and injects them as new
human messages before the next model call.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentState, before_model
from langgraph.config import get_config, get_store
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)


class LinearNotifyState(AgentState):
    """Extended agent state for tracking Linear notifications."""

    linear_messages_sent_count: int


@before_model(state_schema=LinearNotifyState)
async def check_message_queue_before_model(  # noqa: PLR0911
    state: LinearNotifyState,  # noqa: ARG001
    runtime: Runtime,  # noqa: ARG001
) -> dict[str, Any] | None:
    """Middleware that checks for queued messages before each model call.

    If messages are found in the queue for this thread, it extracts all messages,
    adds them to the conversation state as new human messages, and clears the queue.
    Messages are processed in FIFO order (oldest first).

    This enables handling of follow-up comments that arrive while the agent is busy.
    The agent will see the new messages and can incorporate them into its response.
    """
    try:
        config = get_config()
        configurable = config.get("configurable", {})
        thread_id = configurable.get("thread_id")

        if not thread_id:
            return None

        try:
            store = get_store()
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not get store from context: %s", e)
            return None

        if store is None:
            return None

        namespace = ("queue", thread_id)

        try:
            queued_item = await store.aget(namespace, "pending_messages")
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to get queued item: %s", e)
            return None

        if queued_item is None:
            return None

        queued_value = queued_item.value
        queued_messages = queued_value.get("messages", [])

        # Delete early to prevent duplicate processing if middleware runs again
        await store.adelete(namespace, "pending_messages")

        if not queued_messages:
            return None

        logger.info(
            "Found %d queued message(s) for thread %s, injecting into state",
            len(queued_messages),
            thread_id,
        )

        content_blocks = [
            {"type": "text", "text": msg.get("content", "")}
            for msg in queued_messages
            if msg.get("content")
        ]

        if not content_blocks:
            return None

        new_message = {
            "role": "user",
            "content": content_blocks,
        }

        logger.info(
            "Injected %d queued message(s) into state for thread %s",
            len(content_blocks),
            thread_id,
        )

        return {"messages": [new_message]}  # noqa: TRY300
    except Exception:
        logger.exception("Error in check_message_queue_before_model")
    return None
