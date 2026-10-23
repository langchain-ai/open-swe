"""What is sent to a thread while its agent runs, taken before each model call.

A person's follow-ups and the notes background work leaves for the thread wait on
the follow-up queue; events the thread subscribed to and task messages wait as
event matches and task messages. A run takes all of them before each model call,
so the agent sees them without waiting for the run to end, and only they travel:
the conversation stays with the run.
"""

import logging
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any, cast

import httpx2
from langgraph.config import get_config
from langgraph_sdk import get_client

from openswe.dashboard.options import model_supports_images
from openswe.database import postgres
from openswe.input_messages import (
    PersonIdentity,
    RunMessage,
    SystemIdentity,
    build_input_messages,
)
from openswe.message_queue import QueuedMessage
from openswe.middleware.require_user_reply import (
    SLACK_REPLY_SURFACE,
    WEB_REPLY_SURFACE,
    ReplySurface,
)
from openswe.tasks.messages import TaskMessage
from openswe.users import User
from openswe.utils.dashboard_handoff import (
    DASHBOARD_HANDOFF_BODY,
    RELAYED_FROM_KEY,
    dashboard_input_context,
)
from openswe.utils.http import DEFAULT_HTTP_TIMEOUT
from openswe.utils.http_pool import pooled_transport
from openswe.utils.multimodal import fetch_image_block, vision_not_supported_warning
from openswe.webhooks.event_matches import EventMatch

logger = logging.getLogger(__name__)


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
    async with httpx2.AsyncClient(
        transport=pooled_transport(), timeout=DEFAULT_HTTP_TIMEOUT
    ) as client:
        for image_url in image_urls:
            image_block = await fetch_image_block(image_url, client)
            if image_block:
                blocks.append(cast(dict[str, Any], image_block))
    return blocks


def _is_dashboard_queued_message(content: object) -> bool:
    return isinstance(content, dict) and content.get("source") == "dashboard"


_QUEUE_SYSTEM: SystemIdentity = {
    "id": "system:thread-queue",
    "display_name": "Queued message",
    "platform": "open-swe",
}


# Each structured envelope has to arrive as its own message: the transcript
# parses one envelope per message, so packing several into one message's blocks
# renders the concatenation as raw XML.
def _merge_text_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    texts = [
        block["text"]
        for block in blocks
        if block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"]
    ]
    others = [block for block in blocks if block.get("type") != "text"]
    merged: list[dict[str, Any]] = [{"type": "text", "text": "\n\n".join(texts)}] if texts else []
    return merged + others


def _flush_blocks(
    messages: list[dict[str, Any]], blocks: list[dict[str, Any]], injected: set[str]
) -> None:
    if not blocks:
        return
    messages.extend(
        cast(
            list[dict[str, Any]],
            build_input_messages(
                _merge_text_blocks(blocks),
                {"sender_id": _QUEUE_SYSTEM["id"], "surface": "automation", "kind": "system"},
                systems=[_QUEUE_SYSTEM],
                injected_dynamic_context_hashes=injected,
            ),
        )
    )
    blocks.clear()


@dataclass
class Taken:
    """What a run took from its thread's inbox."""

    messages: list[RunMessage] = field(default_factory=list)
    # The surface replies move to, when a follow-up moved them.
    reply_surface: ReplySurface | None = None


class ThreadInbox:
    """One thread's inbox; ``events`` takes event matches and task messages besides follow-ups."""

    def __init__(self, thread_id: str, *, events: bool) -> None:
        self.thread_id = thread_id
        self.events = events

    async def take(self, delivered: Collection[str], surface: ReplySurface) -> Taken:
        """Everything owed besides ``delivered``, the event ids the thread already holds."""
        taken = Taken()
        if self.events and postgres.configured():
            owed = await TaskMessage.owed_besides(self.thread_id, delivered)
            taken.messages.extend(TaskMessage.messages(owed))
        try:
            await self._take_follow_ups(taken, surface)
        except Exception:
            logger.exception(
                "Taking queued follow-ups failed", extra={"agent_thread_id": self.thread_id}
            )
        if self.events and postgres.configured():
            try:
                matches = await EventMatch.owed_besides(self.thread_id, delivered)
            except Exception:
                logger.warning(
                    "Loading owed event matches failed",
                    extra={"agent_thread_id": self.thread_id},
                    exc_info=True,
                )
            else:
                taken.messages.extend(EventMatch.messages(matches))
        return taken

    async def _take_follow_ups(self, taken: Taken, surface: ReplySurface) -> None:  # noqa: C901, PLR0912
        """Follow-ups queued for the thread, oldest first, taken off the queue once built."""
        queued_updates: list[dict[str, Any]] = []
        moved: ReplySurface | None = None
        content_blocks: list[dict[str, Any]] = []
        # Each batch introduces its senders again: the run, not the backend, holds what it saw.
        injected: set[str] = set()

        # A snapshot: what this call consumes, whatever is queued meanwhile.
        queued_messages = await QueuedMessage.for_thread(self.thread_id)
        contents = [message.content for message in queued_messages]
        if not contents:
            return

        logger.info(
            "Taking queued follow-ups",
            extra={"agent_thread_id": self.thread_id, "count": len(contents)},
        )

        has_images = any(
            isinstance(content, dict) and (content.get("image_urls") or content.get("images"))
            for content in contents
        )
        resolved_model_id: str | None = None
        configurable = get_config().get("configurable", {}) if has_images else {}
        if has_images and not configurable.get("image_model_fallback_enabled"):
            # This run's own model first: thread metadata already names the
            # model of any follow-up queued behind it.
            run_model = configurable.get("resolved_agent_model_id") or configurable.get(
                "agent_model_id"
            )
            resolved_model_id = (
                run_model
                if isinstance(run_model, str) and run_model
                else await _resolve_thread_model_id(self.thread_id)
            )

        for content in contents:
            if _is_dashboard_queued_message(content):
                _flush_blocks(queued_updates, content_blocks, injected)
                # Only the move itself is worth announcing. Re-announcing it on
                # every later web follow-up stacks identical handoff notices in
                # the dashboard stream.
                if surface == SLACK_REPLY_SURFACE:
                    queued_updates.extend(
                        cast(
                            list[dict[str, Any]],
                            build_input_messages(
                                DASHBOARD_HANDOFF_BODY,
                                {
                                    "sender_id": "system:dashboard-handoff",
                                    "surface": "automation",
                                    "kind": "system",
                                },
                                systems=[
                                    {
                                        "id": "system:dashboard-handoff",
                                        "display_name": "Dashboard handoff",
                                        "platform": "open-swe",
                                    }
                                ],
                                injected_dynamic_context_hashes=injected,
                            ),
                        )
                    )
                surface = moved = WEB_REPLY_SURFACE
            if isinstance(content, dict) and (
                "text" in content or "image_urls" in content or "images" in content
            ):
                blocks = await _build_blocks_from_payload(content, model_id=resolved_model_id)
                sender = content.get("sender")
                if isinstance(sender, dict) and isinstance(sender.get("id"), str):
                    person: PersonIdentity = {"id": sender["id"]}
                    for key in ("github_login", "email"):
                        value = sender.get(key)
                        if isinstance(value, str):
                            cast(dict[str, str], person)[key] = value
                    person = await User.canonical_person(person)
                    relayed_from = content.get(RELAYED_FROM_KEY)
                    structured = build_input_messages(
                        blocks,
                        dashboard_input_context(
                            person["id"], relayed_from if isinstance(relayed_from, str) else None
                        ),
                        injected_dynamic_context_hashes=injected,
                    )
                    _flush_blocks(queued_updates, content_blocks, injected)
                    queue_id = content.get("queue_id")
                    if isinstance(queue_id, str) and structured:
                        structured[-1]["id"] = queue_id
                    queued_updates.extend(cast(list[dict[str, Any]], structured))
                else:
                    content_blocks.extend(blocks)
                continue
            if isinstance(content, list):
                content_blocks.extend(content)
                continue
            if isinstance(content, str) and content:
                content_blocks.append({"type": "text", "text": content})

        _flush_blocks(queued_updates, content_blocks, injected)
        # Cleared only once every message is built: a failure above leaves
        # them for the next model call instead of losing them.
        await QueuedMessage.remove(queued_messages)
        taken.messages.extend(cast(list[RunMessage], queued_updates))
        taken.reply_surface = moved
