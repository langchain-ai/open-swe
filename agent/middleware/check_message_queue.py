"""Before-model middleware that injects queued messages into state.

Checks the LangGraph store for pending messages (e.g. follow-up Linear
comments that arrived while the agent was busy) and injects them as new
human messages before the next model call.
"""

from __future__ import annotations

import logging
from typing import Any, NotRequired, cast

import httpx
from langchain.agents.middleware import AgentState, before_model
from langgraph.config import get_config, get_store
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from langgraph_sdk import get_client

from ..dashboard.options import model_supports_images
from ..utils.dashboard_handoff import (  # noqa: F401
    DASHBOARD_HANDOFF_INSTRUCTION,
    DASHBOARD_HANDOFF_MARKER,
)
from ..utils.http import DEFAULT_HTTP_TIMEOUT
from ..utils.multimodal import fetch_image_block, vision_not_supported_warning

logger = logging.getLogger(__name__)


class LinearNotifyState(AgentState):
    """Extended agent state for tracking Linear notifications."""

    linear_messages_sent_count: int
    plan_approval_blocked: NotRequired[bool]


async def _resolve_thread_model_id(thread_id: str) -> str | None:
    """Read the resolved model from thread metadata (set by ``get_agent``)."""
    try:
        client = get_client()
        thread = await client.threads.get(thread_id)
        metadata = thread.get("metadata") if isinstance(thread, dict) else None
        if not isinstance(metadata, dict):
            return None
        model = metadata.get("model")
        return model if isinstance(model, str) and model else None
    except Exception:
        logger.debug("Could not read thread metadata for model resolution", exc_info=True)
        return None


async def _build_blocks_from_payload(
    payload: dict[str, Any],
    *,
    model_id: str | None = None,
) -> list[dict[str, Any]]:
    text = payload.get("text", "")
    image_urls = payload.get("image_urls", []) or []
    images = payload.get("images", []) or []
    blocks: list[dict[str, Any]] = []
    if text:
        blocks.append({"type": "text", "text": text})
    if isinstance(images, list):
        blocks.extend(image for image in images if isinstance(image, dict))

    if not image_urls:
        return blocks
    if model_id and not model_supports_images(model_id):
        logger.warning(
            "Skipping %d queued image(s): model %s does not support images",
            len(image_urls),
            model_id,
        )
        if text:
            blocks[0] = {
                "type": "text",
                "text": text + vision_not_supported_warning(model_id, len(image_urls)),
            }
        return blocks
    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
        for image_url in image_urls:
            image_block = await fetch_image_block(image_url, client)
            if image_block:
                blocks.append(cast(dict[str, Any], image_block))
    return blocks


def _is_dashboard_queued_message(content: object) -> bool:
    return isinstance(content, dict) and content.get("source") == "dashboard"


def _dashboard_queued_message_from_owner(content: object) -> bool:
    return isinstance(content, dict) and content.get("from_owner") is True


def _message_update(
    content_blocks: list[dict[str, Any]],
    thread_id: str,
    *,
    plan_approval_blocked: bool | None = None,
) -> dict[str, Any] | None:
    if not content_blocks:
        return None
    logger.info(
        "Injected %d queued message block(s) into state for thread %s",
        len(content_blocks),
        thread_id,
    )
    update: dict[str, Any] = {"messages": [{"role": "user", "content": content_blocks}]}
    if plan_approval_blocked is not None:
        update["plan_approval_blocked"] = plan_approval_blocked
    return update


async def _consume_pending_autofix_event(store: BaseStore, thread_id: str) -> str | None:
    """Pull and clear a batched PR-babysitting event from the store (no thread fetch)."""
    namespace = ("autofix", thread_id)
    try:
        item = await store.aget(namespace, "pending_event")
    except Exception:  # noqa: BLE001
        logger.debug(
            "Could not read pending auto-fix event for thread %s", thread_id, exc_info=True
        )
        return None
    if item is None or not item.value.get("reason"):
        return None
    try:
        await store.adelete(namespace, "pending_event")
    except Exception:  # noqa: BLE001
        logger.debug(
            "Could not clear pending auto-fix event for thread %s", thread_id, exc_info=True
        )
    message = (
        "A PR babysitting event arrived while you were already working on this PR. "
        "Do not start a separate run for that event. Before finishing, re-check the "
        "PR's latest CI status and review comments, then address any newly failed "
        "checks or actionable comments that are clear and deterministic."
    )
    details = item.value.get("details")
    if isinstance(details, list):
        joined = "\n\n".join(d for d in details if isinstance(d, str) and d)
        if joined:
            message += "\n\nNewly arrived feedback to address:\n" + joined
    return message


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

        content_blocks: list[dict[str, Any]] = []
        plan_approval_blocked: bool | None = None
        pending_autofix = await _consume_pending_autofix_event(store, thread_id)
        if pending_autofix:
            content_blocks.append({"type": "text", "text": pending_autofix})

        namespace = ("queue", thread_id)

        try:
            queued_item = await store.aget(namespace, "pending_messages")
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to get queued item: %s", e)
            return _message_update(content_blocks, thread_id)

        if queued_item is None:
            return _message_update(content_blocks, thread_id)

        queued_value = queued_item.value
        queued_messages = queued_value.get("messages", [])

        # Delete early to prevent duplicate processing if middleware runs again
        await store.adelete(namespace, "pending_messages")

        if not queued_messages:
            return _message_update(content_blocks, thread_id)

        logger.info(
            "Found %d queued message(s) for thread %s, injecting into state",
            len(queued_messages),
            thread_id,
        )

        has_images = any(
            isinstance(msg.get("content"), dict)
            and (msg["content"].get("image_urls") or msg["content"].get("images"))
            for msg in queued_messages
        )
        resolved_model_id: str | None = None
        if has_images:
            resolved_model_id = await _resolve_thread_model_id(thread_id)

        for msg in queued_messages:
            content = msg.get("content")
            if _is_dashboard_queued_message(content):
                content_blocks.append({"type": "text", "text": DASHBOARD_HANDOFF_INSTRUCTION})
                if _dashboard_queued_message_from_owner(content):
                    plan_approval_blocked = False
                elif plan_approval_blocked is None:
                    plan_approval_blocked = True
            if isinstance(content, dict) and (
                "text" in content or "image_urls" in content or "images" in content
            ):
                logger.debug("Queued message contains text + image URLs")
                blocks = await _build_blocks_from_payload(content, model_id=resolved_model_id)
                content_blocks.extend(blocks)
                continue
            if isinstance(content, list):
                logger.debug("Queued message contains %d content block(s)", len(content))
                content_blocks.extend(content)
                continue
            if isinstance(content, str) and content:
                logger.debug("Queued message contains text content")
                content_blocks.append({"type": "text", "text": content})

        return _message_update(
            content_blocks, thread_id, plan_approval_blocked=plan_approval_blocked
        )  # noqa: TRY300
    except Exception:
        logger.exception("Error in check_message_queue_before_model")
    return None
