"""What is sent to a thread while its agent runs, read before each model call.

A person's follow-ups and the notes background work leaves for the thread wait on
the follow-up queue; events the thread subscribed to and task messages wait as
event matches and task messages. A run reads all of them before each model call,
so the agent sees them without waiting for the run to end, and only they travel:
the conversation stays with the run.

Reading takes nothing, and asks the run for nothing but who it is and how its plan
reads the inbox (``InboxSpec``): the backend keeps what each thread is owed. All of it stays owed until the run says the model
call it read it for goes ahead (``receive``), or names it received on its next
read, so a run that ends first leaves it for the next. A run started to carry events receives them as it is prepared.
"""

import asyncio
import logging
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

import httpx2
from langgraph_sdk import get_client
from pydantic import JsonValue
from runtime_api.catalog import InboxSpec

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
    RELAY_RUN_ID_KEY,
    RELAYED_FROM_KEY,
    REPLIES_KEY,
    dashboard_input_context,
    passage_replies,
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
    """What a run read from its thread's inbox."""

    messages: list[RunMessage] = field(default_factory=list)
    # The surface replies move to, when a follow-up moved them.
    reply_surface: ReplySurface | None = None
    # The queue entries the follow-ups among ``messages`` came from.
    queued: list[int] = field(default_factory=list)
    # The event matches and task messages among ``messages``.
    events: list[UUID] = field(default_factory=list)
    # The agent thread that relayed the latest follow-up, if one did.
    relayed_from: str | None = None
    relay_updated: bool = False

    def state_update(self, run_id: str | None) -> dict[str, JsonValue]:
        """The run state these follow-ups set.

        The relay source is stamped with the run that read it: state is
        checkpointed, so a later run must not inherit an earlier run's relay.
        """
        update: dict[str, JsonValue] = {}
        if self.reply_surface is not None:
            update["reply_surface"] = self.reply_surface
        if self.relay_updated:
            update[RELAYED_FROM_KEY] = self.relayed_from
            update[RELAY_RUN_ID_KEY] = run_id
        return update


class ThreadInbox:
    """One thread's inbox, read as each run's ``InboxSpec`` says.

    The spec's ``surface`` is where the run starts answering: a run that starts on
    Slack is told when a person's dashboard follow-up moves its answers to the web.
    """

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id

    async def read(self, run_id: str, spec: InboxSpec) -> Taken:
        """Everything the thread is owed, for ``run_id``'s next model call."""
        taken = Taken()
        # Each read takes a connection of its own.
        messages, matches, _ = await asyncio.gather(
            self._task_messages(spec),
            self._event_matches(spec),
            self._follow_ups(taken, run_id, spec),
        )
        taken.messages[:0] = TaskMessage.messages(messages)
        taken.messages.extend(EventMatch.messages(matches))
        taken.events.extend(
            [*(message.id for message in messages), *(match.id for match in matches)]
        )
        return taken

    async def _task_messages(self, spec: InboxSpec) -> list[TaskMessage]:
        if not spec.events or not postgres.configured():
            return []
        try:
            return await TaskMessage.owed(self.thread_id)
        except Exception:
            logger.warning(
                "Loading owed task messages failed",
                extra={"agent_thread_id": self.thread_id},
                exc_info=True,
            )
            return []

    async def _event_matches(self, spec: InboxSpec) -> list[EventMatch]:
        if not spec.events or not postgres.configured():
            return []
        try:
            return await EventMatch.owed(self.thread_id)
        except Exception:
            logger.warning(
                "Loading owed event matches failed",
                extra={"agent_thread_id": self.thread_id},
                exc_info=True,
            )
            return []

    async def _follow_ups(self, taken: Taken, run_id: str, spec: InboxSpec) -> None:
        try:
            await self._read_follow_ups(taken, run_id, spec)
        except Exception:
            logger.exception(
                "Reading queued follow-ups failed", extra={"agent_thread_id": self.thread_id}
            )

    async def receive(self, queued: Collection[int] = (), events: Collection[UUID] = ()) -> None:
        """A run holds ``queued`` follow-ups and ``events``: none is owed again."""
        await QueuedMessage.remove(self.thread_id, queued)
        if events and postgres.configured():
            await TaskMessage.received(self.thread_id, events)
            await EventMatch.received(self.thread_id, events)

    async def _read_follow_ups(  # noqa: C901, PLR0912
        self, taken: Taken, run_id: str, spec: InboxSpec
    ) -> None:
        """Follow-ups queued for the thread, oldest first."""
        surface: ReplySurface = spec.surface
        queued_updates: list[dict[str, Any]] = []
        moved: ReplySurface | None = None
        content_blocks: list[dict[str, Any]] = []
        # Each batch introduces its senders again: the run, not the backend, holds what it saw.
        injected: set[str] = set()

        # A snapshot: what this call reads, whatever is queued meanwhile.
        queued_messages = await QueuedMessage.for_thread(self.thread_id)
        contents = [message.content for message in queued_messages]
        if not contents:
            return

        logger.info(
            "Reading queued follow-ups",
            extra={"agent_thread_id": self.thread_id, "count": len(contents)},
        )

        has_images = any(
            isinstance(content, dict) and (content.get("image_urls") or content.get("images"))
            for content in contents
        )
        resolved_model_id: str | None = None
        if has_images and not spec.image_fallback:
            # This run's own model first: thread metadata already names the
            # model of any follow-up queued behind it.
            resolved_model_id = spec.model_id or await _resolve_thread_model_id(self.thread_id)

        for queued in queued_messages:
            content = queued.content
            if (message := queued.run_message) is not None:
                _flush_blocks(queued_updates, content_blocks, injected)
                queued_updates.append(cast(dict[str, Any], message))
                continue
            relayed_from = content.get(RELAYED_FROM_KEY) if isinstance(content, dict) else None
            taken.relayed_from = relayed_from if isinstance(relayed_from, str) else None
            taken.relay_updated = True
            if _is_dashboard_queued_message(content):
                _flush_blocks(queued_updates, content_blocks, injected)
                # Only the move itself is worth announcing. Re-announcing it on
                # every later web follow-up stacks identical handoff notices in
                # the dashboard stream, so the run's notice keeps one id: a later
                # batch's copy replaces it where it is.
                if surface == SLACK_REPLY_SURFACE:
                    handoff = build_input_messages(
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
                    )
                    for index, message in enumerate(handoff):
                        message["id"] = f"dashboard-handoff:{run_id}:{index}"
                    queued_updates.extend(cast(list[dict[str, Any]], handoff))
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
                            person["id"],
                            relayed_from if isinstance(relayed_from, str) else None,
                            passage_replies(content.get(REPLIES_KEY)),
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
        taken.messages.extend(cast(list[RunMessage], queued_updates))
        taken.reply_surface = moved
        taken.queued = [message.seq for message in queued_messages]
