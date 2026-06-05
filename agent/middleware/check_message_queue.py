"""Before-model middleware that injects queued messages into state.

Checks the LangGraph store for pending messages (e.g. follow-up Linear
comments that arrived while the agent was busy) and injects them as new
human messages before the next model call.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from langchain.agents.middleware import AgentState, before_model
from langgraph.config import get_config, get_store
from langgraph.runtime import Runtime

from ..utils.multimodal import fetch_image_block

logger = logging.getLogger(__name__)

DASHBOARD_HANDOFF_MARKER = "[Open SWE Web handoff]"
DASHBOARD_HANDOFF_INSTRUCTION = (
    f"{DASHBOARD_HANDOFF_MARKER} This follow-up was sent from Web. "
    "The conversation has moved to Web, so answer in the dashboard stream with a normal "
    "assistant message. Do not call slack_thread_reply unless a later Slack message explicitly "
    "moves the conversation back to Slack."
)


_QUEUED_CONFIGURABLE_KEYS = frozenset(
    {
        "reviewer_event",
        "finding_reply_id",
        "finding_reply_author",
        "finding_reply_body",
        "finding_reply_allow_prompt_learning",
        "repo",
    }
)


def _queued_configurable_update(message: dict[str, Any]) -> dict[str, Any]:
    raw_update = message.get("configurable")
    if not isinstance(raw_update, dict):
        return {}

    update: dict[str, Any] = {}
    for key in _QUEUED_CONFIGURABLE_KEYS:
        if key not in raw_update:
            continue
        value = raw_update[key]
        if key == "finding_reply_allow_prompt_learning":
            update[key] = bool(value)
            continue
        if key == "repo":
            if not isinstance(value, dict):
                continue
            owner = value.get("owner")
            name = value.get("name")
            if isinstance(owner, str) and isinstance(name, str):
                update[key] = {"owner": owner, "name": name}
            continue
        if isinstance(value, str):
            update[key] = value
    return update


def _select_queued_messages(
    queued_messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if not queued_messages:
        return [], [], {}

    first_update = _queued_configurable_update(queued_messages[0])
    if first_update:
        return [queued_messages[0]], queued_messages[1:], first_update

    for index, message in enumerate(queued_messages):
        if _queued_configurable_update(message):
            return queued_messages[:index], queued_messages[index:], {}
    return queued_messages, [], {}


class LinearNotifyState(AgentState):
    """Extended agent state for tracking Linear notifications."""

    linear_messages_sent_count: int


async def _build_blocks_from_payload(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    text = payload.get("text", "")
    image_urls = payload.get("image_urls", []) or []
    blocks: list[dict[str, Any]] = []
    if text:
        blocks.append({"type": "text", "text": text})

    if not image_urls:
        return blocks
    async with httpx.AsyncClient() as client:
        for image_url in image_urls:
            image_block = await fetch_image_block(image_url, client)
            if image_block:
                blocks.append(image_block)
    return blocks


def _is_dashboard_queued_message(content: object) -> bool:
    return isinstance(content, dict) and content.get("source") == "dashboard"


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
        raw_queued_messages = (
            queued_value.get("messages") if isinstance(queued_value, dict) else None
        )
        queued_messages = (
            [msg for msg in raw_queued_messages if isinstance(msg, dict)]
            if isinstance(raw_queued_messages, list)
            else []
        )

        queued_messages, remaining_messages, configurable_update = _select_queued_messages(
            queued_messages
        )
        if remaining_messages:
            await store.aput(namespace, "pending_messages", {"messages": remaining_messages})
        else:
            await store.adelete(namespace, "pending_messages")

        if not queued_messages:
            return None

        if configurable_update:
            configurable.update(configurable_update)

        logger.info(
            "Found %d queued message(s) for thread %s, injecting into state",
            len(queued_messages),
            thread_id,
        )

        content_blocks: list[dict[str, Any]] = []
        for msg in queued_messages:
            content = msg.get("content")
            if _is_dashboard_queued_message(content):
                content_blocks.append({"type": "text", "text": DASHBOARD_HANDOFF_INSTRUCTION})
            if isinstance(content, dict) and ("text" in content or "image_urls" in content):
                logger.debug("Queued message contains text + image URLs")
                blocks = await _build_blocks_from_payload(content)
                content_blocks.extend(blocks)
                continue
            if isinstance(content, list):
                logger.debug("Queued message contains %d content block(s)", len(content))
                content_blocks.extend(content)
                continue
            if isinstance(content, str) and content:
                logger.debug("Queued message contains text content")
                content_blocks.append({"type": "text", "text": content})

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
