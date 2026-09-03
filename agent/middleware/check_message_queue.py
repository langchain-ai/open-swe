"""Before-model middleware that injects queued messages into state.

Checks the LangGraph store for pending messages (follow-ups that arrived
while the agent was busy) and injects them as new human messages before the
next model call.
"""

import logging
from collections.abc import Mapping
from typing import Any, cast

from langchain.agents.middleware import AgentState, before_model
from langgraph.config import get_store
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore

from agent.input_messages import (
    InputMessageContext,
    MessageKind,
    RunMessage,
    Surface,
    SystemIdentity,
    build_input_messages,
    visible_dynamic_context_hashes,
)
from agent.media import MediaRef, media_data
from agent.run_config import RunConfig
from agent.utils.dashboard_handoff import DASHBOARD_HANDOFF_BODY
from agent.utils.thread_ops import QueuedMessage

logger = logging.getLogger(__name__)


class LinearNotifyState(AgentState):
    """Extended agent state for tracking Linear notifications."""

    linear_messages_sent_count: int


_QUEUE_SYSTEM: SystemIdentity = {
    "id": "system:thread-queue",
    "display_name": "Queued message",
    "platform": "open-swe",
}
_DASHBOARD_HANDOFF_SYSTEM: SystemIdentity = {
    "id": "system:dashboard-handoff",
    "display_name": "Dashboard handoff",
    "platform": "open-swe",
}


def _context(
    sender_id: str, surface: Surface, kind: MessageKind, media: list[MediaRef]
) -> InputMessageContext:
    context: InputMessageContext = {"sender_id": sender_id, "surface": surface, "kind": kind}
    if media:
        context["data"] = media_data(media)
    return context


class _QueuedUpdates:
    """Messages to inject, with unattributed notices batched into one envelope.

    Each structured envelope has to arrive as its own message: the transcript
    parses one envelope per message, so several packed into one message's
    blocks render as raw XML. Notices without a sender share a single system
    envelope instead.
    """

    def __init__(self, injected: set[str]) -> None:
        self.messages: list[RunMessage] = []
        self._injected = injected
        self._notices: list[str] = []
        self._notice_media: list[MediaRef] = []

    def notice(self, text: str, media: list[MediaRef] | None = None) -> None:
        if text:
            self._notices.append(text)
        self._notice_media.extend(media or [])

    def envelope(self, text: str, context: InputMessageContext, **identities: Any) -> None:
        self.flush()
        self.messages.extend(
            build_input_messages(
                text, context, injected_dynamic_context_hashes=self._injected, **identities
            )
        )

    def flush(self) -> None:
        if not self._notices and not self._notice_media:
            return
        self.messages.extend(
            build_input_messages(
                "\n\n".join(self._notices),
                _context(_QUEUE_SYSTEM["id"], "automation", "system", self._notice_media),
                systems=[_QUEUE_SYSTEM],
                injected_dynamic_context_hashes=self._injected,
            )
        )
        self._notices.clear()
        self._notice_media.clear()


async def _consume_pending_autofix_event(store: BaseStore, thread_id: str) -> str | None:
    """Pull and clear a batched PR-babysitting event from the store (no thread fetch)."""
    namespace = ("autofix", thread_id)
    try:
        item = await store.aget(namespace, "pending_event")
    except Exception:  # noqa: BLE001
        logger.debug(
            "Could not read pending auto-fix event", extra={"thread_id": thread_id}, exc_info=True
        )
        return None
    if item is None or not item.value.get("reason"):
        return None
    try:
        await store.adelete(namespace, "pending_event")
    except Exception:  # noqa: BLE001
        logger.debug(
            "Could not clear pending auto-fix event", extra={"thread_id": thread_id}, exc_info=True
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


async def _take_queued_messages(store: BaseStore, thread_id: str) -> list[QueuedMessage]:
    """Pull and clear the thread's pending follow-ups, oldest first."""
    namespace = ("queue", thread_id)
    try:
        item = await store.aget(namespace, "pending_messages")
    except Exception:  # noqa: BLE001
        logger.warning("Failed to read queued messages", extra={"thread_id": thread_id})
        return []
    if item is None:
        return []
    # Delete before processing so a retried model call cannot inject twice.
    await store.adelete(namespace, "pending_messages")
    raw_messages = item.value.get("messages", [])
    if not isinstance(raw_messages, list):
        return []
    queued = [
        message
        for raw in raw_messages
        if isinstance(raw, Mapping) and (message := QueuedMessage.parse(raw.get("content")))
    ]
    if queued:
        logger.info(
            "Found queued messages for thread",
            extra={"thread_id": thread_id, "queued_count": len(queued)},
        )
    return queued


@before_model(state_schema=LinearNotifyState)
async def check_message_queue_before_model(
    state: LinearNotifyState,
    runtime: Runtime,  # noqa: ARG001
) -> dict[str, Any] | None:
    """Inject follow-ups that arrived while the agent was busy, in FIFO order."""
    try:
        thread_id = RunConfig.from_runtime().thread_id
        if not thread_id:
            return None
        try:
            store = get_store()
        except Exception:  # noqa: BLE001
            logger.debug("Could not get store from context", exc_info=True)
            return None
        if store is None:
            return None

        updates = _QueuedUpdates(visible_dynamic_context_hashes(state))
        pending_autofix = await _consume_pending_autofix_event(store, thread_id)
        if pending_autofix:
            updates.notice(pending_autofix)

        for queued in await _take_queued_messages(store, thread_id):
            if queued.source == "dashboard":
                updates.envelope(
                    DASHBOARD_HANDOFF_BODY,
                    _context(_DASHBOARD_HANDOFF_SYSTEM["id"], "automation", "system", []),
                    systems=[_DASHBOARD_HANDOFF_SYSTEM],
                )
            if not queued.text and not queued.media:
                continue
            if queued.sender is None:
                updates.notice(queued.text, queued.media)
            else:
                updates.envelope(
                    queued.text,
                    _context(queued.sender.id, "web", "human", queued.media),
                    people=[queued.sender.identity()],
                )
        updates.flush()
        if not updates.messages:
            return None
        logger.info(
            "Injected queued messages into state",
            extra={"thread_id": thread_id, "injected_count": len(updates.messages)},
        )
        return {"messages": cast(list[Any], updates.messages)}
    except Exception:
        logger.exception("Error in check_message_queue_before_model")
    return None
